"""Message trace (PR 34): read-only, admin-only delivery diagnostics.

Given an address, a Message-ID or a time window, it follows messages by
Postfix queue ID and Message-ID across what mailcow already keeps: the
Postfix log, Rspamd history, the quarantine, the mail queue and Dovecot's
delivery log. It explains each stage it can establish, and says plainly what
it cannot.

It changes nothing: no release, resend or queue action. It reads mailcow
with its own read-only API key (MAILCOW_READ_ONLY_API_KEY), so even a bug
here cannot write. It never returns message content or subjects, only
addresses, IDs, times, verdicts and the servers' replies.

The look-back is mailcow's Redis log window (by default the newest
TRACE_LOG_LINES lines of each log). How many days that covers on the server
is an open check; every answer reports the oldest log line it saw.

Admin CLI, run on the server (calls GET /v1/trace as the `admin` caller):

    docker compose exec -T broker python -m broker.trace --address alice@example.com
    docker compose exec -T broker python -m broker.trace --message-id '<abc@example.com>' --hours 72
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field

import httpx

from .mailcow import MailcowClient, MailcowError

MAX_MESSAGES = 50

LIMITS = [
    "Only the retained log window is searched; older mail leaves no trace here.",
    "Mail that never connected to this server cannot appear at all.",
    "'Accepted by the destination server' does not prove the recipient saw it.",
    "'Delivered to a mailbox' does not prove it was read, and the person may have moved or deleted it since.",
    "Quarantine and queue show the current state, not history.",
]

# --- Postfix log parsing ------------------------------------------------------

QID = r"(?P<qid>[0-9A-F]{6,}|[0-9B-DF-HJ-NP-TV-Zb-df-hj-np-tv-z]{10,})"
RE_CLIENT = re.compile(rf"^{QID}: client=(?P<client>\S+)")
RE_MSGID = re.compile(rf"^{QID}: message-id=(?P<mid><[^>]*>|\S+)")
RE_FROM = re.compile(rf"^{QID}: from=<(?P<from>[^>]*)>")
RE_TO = re.compile(rf"^{QID}: to=<(?P<to>[^>]*)>,(?:.*?)relay=(?P<relay>[^,]+),.*?"
                   rf"(?:dsn=(?P<dsn>[\d.]+), )?status=(?P<status>\w+)(?: \((?P<reply>.*)\))?$")
RE_MILTER = re.compile(rf"^{QID}: milter-reject: \S+ from \S+: (?P<reason>.*?); from=<(?P<from>[^>]*)> to=<(?P<to>[^>]*)>")
RE_NOQUEUE = re.compile(r"^NOQUEUE: (?:reject|milter-reject): \S+ from (?P<client>\S+): (?P<reason>.*?); "
                        r"from=<(?P<from>[^>]*)> to=<(?P<to>[^>]*)>")
RE_REMOVED = re.compile(rf"^{QID}: removed")
RE_DOVECOT_SAVE = re.compile(r"lmtp\((?P<user>[^)]+)\).*?msgid=(?P<mid><[^>]*>|\S+): saved mail to (?P<folder>.+)$")


def _t(entry: dict) -> float:
    try:
        return float(entry.get("time") or entry.get("unix_time") or 0)
    except (TypeError, ValueError):
        return 0.0


def _norm_mid(mid: str) -> str:
    mid = (mid or "").strip()
    if mid and not mid.startswith("<"):
        mid = f"<{mid}>"
    return mid.lower()


def reject_kind(reason: str) -> str:
    low = reason.lower()
    if "blocked using" in low or "listed" in low and "rbl" in low:
        return "blocklist"
    if "user unknown" in low or "unknown in" in low or "recipient address rejected: user" in low:
        return "unknown_recipient"
    if "spam" in low or "milter" in low or "rspamd" in low:
        return "spam_filter"
    return "policy"


@dataclass
class Delivery:
    to: str
    status: str
    relay: str
    reply: str
    time: float
    dsn: str = ""


@dataclass
class Message:
    qid: str
    first_seen: float
    client: str = ""
    message_id: str = ""
    sender: str = ""
    deliveries: list[Delivery] = field(default_factory=list)
    content_reject: str = ""
    content_reject_to: str = ""
    removed: bool = False


def parse_postfix(entries: list[dict]) -> tuple[dict[str, Message], list[dict]]:
    """Group Postfix log lines by queue ID; return messages and NOQUEUE rejections."""
    msgs: dict[str, Message] = {}
    rejects: list[dict] = []
    for e in sorted(entries, key=_t):
        line, t = str(e.get("message", "")), _t(e)
        if m := RE_NOQUEUE.search(line):
            rejects.append({"time": t, "client": m["client"], "from": m["from"].lower(), "to": m["to"].lower(),
                            "reason": m["reason"][:300], "kind": reject_kind(m["reason"])})
            continue
        m = None
        for rx in (RE_CLIENT, RE_MSGID, RE_FROM, RE_TO, RE_MILTER, RE_REMOVED):
            if m := rx.search(line):
                break
        if not m:
            continue
        msg = msgs.setdefault(m["qid"], Message(qid=m["qid"], first_seen=t))
        if rx is RE_CLIENT:
            msg.client = m["client"]
        elif rx is RE_MSGID:
            msg.message_id = _norm_mid(m["mid"])
        elif rx is RE_FROM:
            msg.sender = m["from"].lower()
        elif rx is RE_TO:
            msg.deliveries.append(Delivery(to=m["to"].lower(), status=m["status"], relay=m["relay"],
                                           reply=(m["reply"] or "")[:300], time=t, dsn=m["dsn"] or ""))
        elif rx is RE_MILTER:
            msg.content_reject, msg.content_reject_to = m["reason"][:300], m["to"].lower()
            msg.sender = msg.sender or m["from"].lower()
        elif rx is RE_REMOVED:
            msg.removed = True
    return msgs, rejects


def _symbols(row: dict, n: int = 5) -> list[str]:
    syms = row.get("symbols") or {}
    items = []
    for name, s in (syms.items() if isinstance(syms, dict) else []):
        score = s.get("score", 0) if isinstance(s, dict) else 0
        try:
            items.append((float(score), str(name)))
        except (TypeError, ValueError):
            continue
    return [f"{name} ({score:+.1f})" for score, name in sorted(items, reverse=True)[:n] if score > 0]


def _queue_recipients(entry: dict) -> list[tuple[str, str]]:
    out = []
    for r in entry.get("recipients") or []:
        if isinstance(r, dict):
            out.append((str(r.get("address", "")).lower(), str(r.get("delay_reason", ""))[:300]))
        else:
            out.append((str(r).split(" ", 1)[0].lower(), ""))
    return out


# --- correlation ---------------------------------------------------------------

def trace(*, postfix: list[dict], rspamd: list[dict], dovecot: list[dict], quarantine: list[dict],
          queue: list[dict], address: str | None, message_id: str | None, since: float, until: float) -> dict:
    """Pure function: correlate what mailcow kept into one explanation per message."""
    addr = (address or "").lower() or None
    mid = _norm_mid(message_id) if message_id else None
    msgs, rejects = parse_postfix(postfix)
    by_qid_rspamd = {str(r.get("qid")): r for r in rspamd if r.get("qid")}
    by_mid_rspamd = {_norm_mid(str(r.get("message-id"))): r for r in rspamd if r.get("message-id")}
    quarantined = {str(q.get("qid")): q for q in quarantine if q.get("qid")}
    queued = {str(q.get("queue_id")): q for q in queue if q.get("queue_id")}
    saved: dict[tuple[str, str], str] = {}
    for e in dovecot:
        if m := RE_DOVECOT_SAVE.search(str(e.get("message", ""))):
            saved[(_norm_mid(m["mid"]), m["user"].lower())] = m["folder"].strip()[:100]

    def wanted(m: Message) -> bool:
        if not since <= m.first_seen <= until:
            return False
        if mid and m.message_id != mid:
            return False
        if addr and addr != m.sender and addr not in {d.to for d in m.deliveries} and addr != m.content_reject_to:
            return False
        return True

    out = []
    for m in sorted((m for m in msgs.values() if wanted(m)), key=lambda m: m.first_seen, reverse=True):
        stages = [{"stage": "accepted", "time": m.first_seen,
                   "detail": f"Postfix accepted it as queue ID {m.qid}" + (f" from {m.client}" if m.client else "")}]
        rs = by_qid_rspamd.get(m.qid) or (by_mid_rspamd.get(m.message_id) if m.message_id else None)
        if rs:
            action = str(rs.get("action", "unknown"))
            score = rs.get("score")
            syms = _symbols(rs)
            stages.append({"stage": "spam_check", "time": _t(rs),
                           "detail": f"Rspamd: {action}, score {score}" + (f"; main symbols: {', '.join(syms)}" if syms else "")})
        if m.content_reject:
            stages.append({"stage": "rejected_smtp", "time": m.first_seen,
                           "detail": f"Rejected during SMTP after the content check: {m.content_reject}"})
        if m.qid in quarantined:
            q = quarantined[m.qid]
            stages.append({"stage": "quarantined", "time": _t({"time": q.get("created")}),
                           "detail": f"In quarantine for {q.get('rcpt', 'the recipient')} (score {q.get('score')})"})
        for d in m.deliveries:
            local = d.relay.startswith("dovecot")
            if d.status == "sent" and local:
                folder = saved.get((m.message_id, d.to))
                stages.append({"stage": "delivered_local", "time": d.time, "recipient": d.to,
                               "detail": f"Delivered to the mailbox {d.to}" + (f", folder {folder}" if folder else "")})
            elif d.status == "sent":
                stages.append({"stage": "sent_remote", "time": d.time, "recipient": d.to,
                               "detail": f"Accepted by the destination server {d.relay}: {d.reply}"})
            elif d.status == "deferred":
                stages.append({"stage": "deferred", "time": d.time, "recipient": d.to,
                               "detail": f"Delivery deferred, will retry ({d.relay}): {d.reply}"})
            elif d.status in ("bounced", "expired"):
                stages.append({"stage": "bounced", "time": d.time, "recipient": d.to,
                               "detail": f"Delivery failed for good ({d.relay}): {d.reply}"})
        if m.qid in queued:
            for rcpt, why in _queue_recipients(queued[m.qid]):
                stages.append({"stage": "queued", "time": None, "recipient": rcpt,
                               "detail": "Still in the mail queue now" + (f": {why}" if why else "")})
        out.append({"queue_id": m.qid, "message_id": m.message_id or None, "from": m.sender or None,
                    "to": sorted({d.to for d in m.deliveries} | ({m.content_reject_to} if m.content_reject_to else set())),
                    "received_at": m.first_seen, "stages": stages})

    rej = [{"stage": "rejected_smtp", "time": r["time"], "from": r["from"] or None, "to": r["to"],
            "reason_kind": r["kind"], "detail": f"Rejected during SMTP ({r['kind'].replace('_', ' ')}): {r['reason']}"}
           for r in sorted(rejects, key=lambda r: r["time"], reverse=True)
           if since <= r["time"] <= until and not mid and (not addr or addr in (r["from"], r["to"]))]

    oldest = min((_t(e) for e in postfix if _t(e)), default=None)
    found = bool(out or rej)
    summary = []
    if not found:
        covered = f"since {_iso(oldest)}" if oldest else "nothing (no Postfix log lines were available)"
        summary.append({"stage": "never_reached", "time": None,
                        "detail": f"No matching connection, rejection or queue entry in the retained log, which covers {covered}."})
    if oldest and since < oldest:
        summary.append({"stage": "window_short", "time": oldest,
                        "detail": f"The search window starts before the oldest log line ({_iso(oldest)}); earlier mail can't be traced."})
    return {"ok": True, "found": found,
            "window": {"since": since, "until": until, "oldest_log_entry": oldest,
                       "postfix_lines": len(postfix)},
            "messages": out[:MAX_MESSAGES], "rejections": rej[:MAX_MESSAGES],
            "truncated": len(out) > MAX_MESSAGES or len(rej) > MAX_MESSAGES,
            "notes": summary, "limits": LIMITS}


def _iso(t: float | None) -> str:
    import time as _time
    return _time.strftime("%Y-%m-%d %H:%M UTC", _time.gmtime(t)) if t else "unknown"


def collect(mc: MailcowClient, log_lines: int) -> dict:
    """Read everything the trace uses from mailcow (read-only key). Raises MailcowError."""
    return {
        "postfix": mc.logs("postfix", log_lines),
        "rspamd": mc.logs("rspamd-history", min(log_lines, 1000)),
        "dovecot": mc.logs("dovecot", log_lines),
        "quarantine": mc.quarantine(),
        "queue": mc.mail_queue(),
    }


# --- admin CLI -------------------------------------------------------------------

URL = "http://127.0.0.1:8100/v1/trace"


def render(data: dict, out=print) -> None:
    w = data.get("window", {})
    out(f"Searched {w.get('postfix_lines', 0)} Postfix log lines; oldest from {_iso(w.get('oldest_log_entry'))}.")
    for n in data.get("notes", []):
        out(f"! {n['detail']}")
    for m in data.get("messages", []):
        out("")
        out(f"Message {m['queue_id']}  {m.get('message_id') or ''}")
        out(f"  from {m.get('from') or '?'} to {', '.join(m.get('to') or []) or '?'}, received {_iso(m['received_at'])}")
        for s in m["stages"]:
            out(f"  - [{s['stage']}] {_iso(s['time']) if s.get('time') else 'now'}: {s['detail']}")
    for r in data.get("rejections", []):
        out("")
        out(f"Rejected {_iso(r['time'])}: from {r.get('from') or '?'} to {r['to']}")
        out(f"  {r['detail']}")
    if data.get("truncated"):
        out(f"\n(Only the newest {MAX_MESSAGES} are shown; narrow the search.)")
    out("\nWhat this cannot tell you:")
    for lim in data.get("limits", []):
        out(f"  * {lim}")


def main(argv: list[str] | None = None, client: httpx.Client | None = None, out=print) -> int:
    p = argparse.ArgumentParser(prog="python -m broker.trace", description="Read-only message trace (admin).")
    p.add_argument("--address")
    p.add_argument("--message-id")
    p.add_argument("--hours", type=int, default=24)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        out("ADMIN_TOKEN is not set in broker.env; the trace is admin-only.")
        return 2
    params = {k: v for k, v in {"address": a.address, "message_id": a.message_id, "hours": a.hours}.items() if v}
    c = client or httpx.Client()
    try:
        r = c.get(URL, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    except httpx.HTTPError as e:
        out(f"control plane not answering: {type(e).__name__}")
        return 2
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        out(f"refused: HTTP {r.status_code} {data.get('kind', '')} {data.get('detail', '')}")
        return 2
    if a.json:
        out(json.dumps(data, indent=2))
    else:
        render(data, out)
    return 0 if data.get("found") else 1


if __name__ == "__main__":
    sys.exit(main())

__all__ = ["trace", "collect", "parse_postfix", "main", "MailcowError"]
