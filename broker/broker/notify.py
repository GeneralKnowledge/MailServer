"""Sign-in notifications (PR 26): mail a person when their account is signed in
to from a network it hasn't been used from before.

    docker compose exec -T broker python -m broker.notify [--dry-run]

Run every 10 minutes by scripts/notify-signins.sh, only when NOTIFY_SIGNINS=true.

What already exists is used, not rebuilt (ADR 0016): Keycloak records every
browser sign-in as a LOGIN event (realm events on, kept 30 days); mailcow
mails quota warnings itself at 80 % and 95 % (its "Quota notifications"
option). The only thing missing was telling the person, so this job reads
Keycloak's LOGIN events (read-only role view-events), compares the network
(IPv4 /24, IPv6 /48) with the ones that person has used before, and sends one
plain-text mail through mailcow's submission port as a send-only mailbox.

- The first run only learns: it records the last 30 days and mails nobody.
- A person's very first sign-in (right after sign-up) is learnt, not mailed.
- A failed send stops the run at that event; the next run retries from there.
- Mail clients (app passwords) don't sign in through Keycloak and aren't
  covered: their recent logins are on mailcow's own user page (ADR 0015).

Nothing here is reachable over HTTP. Each mail writes one `notify.signin`
audit event (address only; the network is in the mail, not the log), each
run one `notify.run` with counts.

Exit 0: done. 1: a mail could not be sent (retried next run). 2: could not run.
"""

from __future__ import annotations

import argparse
import ipaddress
import smtplib
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from .config import load_settings
from .keycloak import KeycloakClient, KeycloakError
from .store import Store

LEARN_SECONDS = 30 * 86400          # Keycloak's event retention (realm eventsExpiration)
MAX_PER_PERSON_PER_RUN = 3          # a burst of new networks gives at most this many mails

APPS = {
    "mailcow": "webmail and mailbox settings",
    "avedeus-portal": "your account page",
    "avedeus-admin": "the admin console",
    "account-console": "Keycloak's account settings",
}


def place_of(ip: str) -> str:
    """The network a sign-in came from: IPv4 /24, IPv6 /48; 'unknown' if unreadable."""
    try:
        a = ipaddress.ip_address(ip.strip())
    except ValueError:
        return "unknown"
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    prefix = 24 if a.version == 4 else 48
    return str(ipaddress.ip_network(f"{a}/{prefix}", strict=False))


@dataclass
class Mailer:
    """Sends through mailcow's submission port (587, STARTTLS, authenticated),
    checking the certificate against `host`. `connect_to` lets the broker reach
    the port at avedeus-edge's gateway while still checking the real name."""
    host: str
    user: str
    password: str
    port: int = 587
    connect_to: str = ""
    from_name: str = "Avedeus Mail"
    timeout: float = 20.0
    tls: ssl.SSLContext | None = None   # None: the system's trusted CAs; tests pass their own

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = formataddr((self.from_name, self.user))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(usegmt=True)
        msg["Message-ID"] = make_msgid(domain=self.user.rpartition("@")[2] or None)
        msg["Auto-Submitted"] = "auto-generated"   # RFC 3834: no auto-replies to this
        msg.set_content(body)
        with _SMTP(self.host, self.port, self.connect_to, self.timeout) as s:
            s.starttls(context=self.tls or ssl.create_default_context())
            s.login(self.user, self.password)
            s.send_message(msg)


class _SMTP(smtplib.SMTP):
    def __init__(self, host: str, port: int, connect_to: str, timeout: float):
        self._connect_to = connect_to
        super().__init__(host, port, timeout=timeout)   # STARTTLS checks the certificate against host

    def _get_socket(self, host, port, timeout):
        return socket.create_connection((self._connect_to or host, port), timeout)


def _when(ms: int) -> str:
    return time.strftime("%d %B %Y, %H:%M UTC", time.gmtime(ms / 1000))


def message(address: str, event: dict, account_url: str) -> tuple[str, str]:
    app = APPS.get(event["client_id"], "Avedeus Mail")
    body = f"""Your account {address} was just signed in to from a network it hasn't been used from before.

When: {_when(event['time_ms'])}
Where: internet address {event['ip'] or 'unknown'}
Signed in to: {app}

If this was you, for example on a new phone or another Wi-Fi, there is nothing to do.

If it wasn't you, go to your account page and change your password, then sign out
everywhere else (Security, "Where you're signed in"), and tell an administrator:
{account_url}

This message is sent automatically. Replies aren't read.
"""
    return "New sign-in to your Avedeus Mail account", body


def run(kc: KeycloakClient, store: Store, mailer: Mailer | None, *, now: float, account_url: str,
        dry_run: bool = False, out=print) -> int:
    # Where the last run stopped, whether it ended well or at a failed send.
    runs = [r for r in (store.latest("notify.run", "ok"), store.latest("notify.run", "failed"))
            if r and "until_ms" in (r.get("counts") or {})]
    learning = not runs
    since_ms = int((now - LEARN_SECONDS) * 1000) if learning else max(int(r["counts"]["until_ms"]) for r in runs)
    try:
        events = kc.login_events(since_ms)
    except KeycloakError as e:
        out(f"notify: Keycloak sign-in events unavailable: {e.kind}: {e.detail}")
        if not dry_run:
            store.audit(caller="system", action="notify.run", outcome="failed", error_kind=e.kind, adapter=kc.name)
        return 2

    counts = {"events": len(events), "learnt": 0, "sent": 0, "failed": 0, "skipped": 0, "until_ms": since_ms}
    per_person: dict[str, int] = {}
    for ev in events:
        subject, place = ev["user_id"], place_of(ev["ip"])
        known = store.places(subject)
        if place in known:
            if not dry_run:
                store.place_seen(subject, place, ev["time_ms"] / 1000)
            counts["until_ms"] = ev["time_ms"]
            continue
        address = store.address_for(kc.name, subject)
        if learning or not known or address is None:
            # First run, the person's first sign-in, or an identity without a mailbox.
            counts["learnt" if address else "skipped"] += 1
            if not dry_run:
                store.place_seen(subject, place, ev["time_ms"] / 1000)
            counts["until_ms"] = ev["time_ms"]
            continue
        if per_person.get(subject, 0) >= MAX_PER_PERSON_PER_RUN:
            counts["skipped"] += 1
        elif dry_run:
            out(f"  would mail {address}: new network {place} ({ev['client_id']})")
        else:
            subject_line, body = message(address, ev, account_url)
            try:
                assert mailer is not None
                mailer.send(address, subject_line, body)
            except (OSError, smtplib.SMTPException) as e:
                counts["failed"] += 1
                store.audit(caller="system", action="notify.signin", outcome="failed", address=address,
                            error_kind="unavailable", adapter="smtp")
                out(f"  FAILED   {address}: {type(e).__name__}: {e} (the next run retries from here)")
                break
            store.audit(caller="system", action="notify.signin", outcome="ok", address=address, adapter="smtp")
            out(f"  mailed   {address}: new network {place}")
            counts["sent"] += 1
            per_person[subject] = per_person.get(subject, 0) + 1
        if not dry_run:
            store.place_seen(subject, place, ev["time_ms"] / 1000)
        counts["until_ms"] = ev["time_ms"]

    if learning and not events:
        counts["until_ms"] = int(now * 1000)
    if not dry_run:
        store.prune_places()
        store.audit(caller="system", action="notify.run", outcome="failed" if counts["failed"] else "ok",
                    adapter=kc.name, counts=counts)
    out("notify: " + ("first run, learning only; " if learning else "") +
        ", ".join(f"{k} {v}" for k, v in counts.items() if k != "until_ms"))
    return 1 if counts["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m broker.notify",
                                description="Mail people about sign-ins from a new network.")
    p.add_argument("--dry-run", action="store_true", help="say who would be mailed, send and record nothing")
    a = p.parse_args(argv)
    s = load_settings()
    if not s.notify_signins:
        print("notify: NOTIFY_SIGNINS is off; nothing to do")
        return 0
    kc = KeycloakClient(s.keycloak_url, s.keycloak_realm, s.keycloak_client_id, s.keycloak_client_secret)
    mailer = Mailer(s.notify_smtp_host, s.notify_smtp_user, s.notify_smtp_password, s.notify_smtp_port,
                    s.notify_smtp_connect)
    try:
        store = Store(s.db_path)
    except Exception as e:  # noqa: BLE001
        print(f"notify: control-plane database unavailable: {e}")
        return 2
    return run(kc, store, mailer, now=time.time(), account_url=s.notify_account_url, dry_run=a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
