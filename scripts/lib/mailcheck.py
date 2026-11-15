"""Daily delivery and DNS checks (PR 30), run by scripts/delivery-check.sh.

Three parts, each answering "would mail from and to this server work today?":

* DNS: the mail domain's MX points at the mail host; the host's A/AAAA are the
  server's addresses; the IPv4 address's PTR names the host back (forward-
  confirmed reverse DNS); one SPF record that ends in -all or ~all and covers
  the server; the DKIM record exists and (when mailcow's key can be read) is
  the key mailcow signs with; a DMARC record exists.
* Blocklists: the server's addresses and the mail domain on a few widely used
  DNS blocklists. A list that refuses to answer (Spamhaus does for public
  resolvers) is reported as "couldn't check", not as clean or listed.
* Delivery probe (optional, PROBE_TO set): one message to an outside mailbox
  you own, sent through mailcow's submission port; it passes when Postfix
  reports status=sent (the receiving server accepted it). Whether it lands in
  the inbox or in spam is not visible from here: check that mailbox, or use
  mail-tester.com, when it matters.

Uses `dig` (dnsutils, installed by setup.sh) and the standard library only.
Prints one line per check: "ok|PROBLEM|note<TAB>key<TAB>text". Exit 0 when
there is no problem, 1 otherwise, 2 when it couldn't run.
"""

from __future__ import annotations

import base64
import ipaddress
import os
import re
import smtplib
import ssl
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate

# IP blocklists: (zone, name). Answers in 127.0.0.0/8 mean listed, except the
# error codes below.
IP_LISTS = [("zen.spamhaus.org", "Spamhaus ZEN"), ("b.barracudacentral.org", "Barracuda"),
            ("bl.spamcop.net", "SpamCop"), ("psbl.surriel.com", "PSBL")]
DOMAIN_LISTS = [("dbl.spamhaus.org", "Spamhaus DBL")]
# Spamhaus: 127.255.255.252 typo, .254 public/open resolver, .255 excessive queries.
REFUSED = {"127.255.255.252", "127.255.255.254", "127.255.255.255", "127.0.1.255"}


@dataclass
class Report:
    lines: list[tuple[str, str, str]] = field(default_factory=list)

    def ok(self, key, text):
        self.lines.append(("ok", key, text))

    def problem(self, key, text):
        self.lines.append(("PROBLEM", key, text))

    def note(self, key, text):
        self.lines.append(("note", key, text))

    @property
    def problems(self):
        return [line for line in self.lines if line[0] == "PROBLEM"]


def dig(server: str = ""):
    """A resolver using dig; `server` is an optional resolver address."""
    def resolve(name: str, rtype: str) -> list[str]:
        cmd = ["dig", "+short", "+time=3", "+tries=2", rtype, name]
        if server:
            cmd.insert(1, f"@{server}")
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            raise OSError(f"dig {rtype} {name} failed")
        return [line.strip() for line in out.stdout.splitlines() if line.strip() and not line.startswith(";")]
    return resolve


def _txt(records: list[str]) -> list[str]:
    """dig prints TXT as quoted chunks: join them."""
    return ["".join(re.findall(r'"((?:[^"\\]|\\.)*)"', r)) or r for r in records]


def _host(name: str) -> str:
    return name.rstrip(".").lower()


def check_dns(q, domain: str, host: str, ipv4: str = "", ipv6: str = "", selector: str = "dkim",
              dkim_key: str = "", r: Report | None = None) -> Report:
    r = r or Report()
    # MX
    mx = [x.split() for x in q(domain, "MX")]
    targets = sorted((int(p), _host(t)) for p, t in (m for m in mx if len(m) == 2))
    if not targets:
        r.problem("dns:mx", f"{domain} has no MX record")
    elif _host(host) not in [t for _, t in targets]:
        r.problem("dns:mx", f"{domain} MX is {', '.join(t for _, t in targets)}, not {host}")
    else:
        r.ok("dns:mx", f"{domain} MX -> {host}")
    # A / AAAA
    a = [x for x in q(host, "A") if re.fullmatch(r"[\d.]+", x)]
    aaaa = [x for x in q(host, "AAAA") if ":" in x]
    if not a:
        r.problem("dns:a", f"{host} has no A record")
    elif ipv4 and ipv4 not in a:
        r.problem("dns:a", f"{host} A is {', '.join(a)}, not this server ({ipv4})")
    else:
        r.ok("dns:a", f"{host} A {', '.join(a)}")
    ipv4 = ipv4 or (a[0] if a else "")
    if ipv6:
        if ipv6 not in aaaa:
            r.problem("dns:aaaa", f"{host} AAAA is {', '.join(aaaa) or 'missing'}, not this server ({ipv6})")
        else:
            r.ok("dns:aaaa", f"{host} AAAA {ipv6}")
    elif aaaa:
        r.note("dns:aaaa", f"{host} has AAAA {', '.join(aaaa)}; set PUBLIC_IPV6 to check it is this server")
    # PTR (forward-confirmed)
    if ipv4:
        ptr = [_host(x) for x in q(ipaddress.ip_address(ipv4).reverse_pointer, "PTR")]
        if _host(host) in ptr:
            r.ok("dns:ptr", f"{ipv4} PTR -> {host}")
        else:
            r.problem("dns:ptr", f"{ipv4} PTR is {', '.join(ptr) or 'missing'}, not {host} (set it at the hosting provider)")
    # SPF
    spf = [t for t in _txt(q(domain, "TXT")) if t.lower().startswith("v=spf1")]
    if not spf:
        r.problem("dns:spf", f"{domain} has no SPF record")
    elif len(spf) > 1:
        r.problem("dns:spf", f"{domain} has {len(spf)} SPF records; receivers treat that as an error")
    else:
        s = spf[0].lower().split()
        covered = "mx" in s or "a" in s or (ipv4 and f"ip4:{ipv4}" in s) or any(x.startswith("include:") for x in s)
        if s[-1] not in ("-all", "~all"):
            r.problem("dns:spf", f"{domain} SPF ends in {s[-1]!r}, not -all or ~all")
        elif not covered:
            r.problem("dns:spf", f"{domain} SPF does not cover this server: {spf[0]}")
        else:
            r.ok("dns:spf", f"{domain} SPF {spf[0]}")
    # DKIM
    rec = "".join(_txt(q(f"{selector}._domainkey.{domain}", "TXT")))
    m = re.search(r"(?:^|;)\s*p=([A-Za-z0-9+/=\s]*)", rec)
    if not rec or not m or not m.group(1).strip():
        r.problem("dns:dkim", f"{selector}._domainkey.{domain} has no DKIM key")
    else:
        published = re.sub(r"\s", "", m.group(1))
        try:
            base64.b64decode(published, validate=True)
        except ValueError:
            r.problem("dns:dkim", f"{selector}._domainkey.{domain}: the key is not valid base64")
        else:
            if dkim_key and re.sub(r"\s", "", dkim_key) != published:
                r.problem("dns:dkim", f"{selector}._domainkey.{domain} is not the key mailcow signs with "
                                      "(copy it again from mailcow: Configuration -> ARC/DKIM keys)")
            elif dkim_key:
                r.ok("dns:dkim", f"{selector}._domainkey.{domain} matches mailcow's key")
            else:
                r.ok("dns:dkim", f"{selector}._domainkey.{domain} has a key (mailcow's key not read, so not compared)")
    # DMARC
    dmarc = [t for t in _txt(q(f"_dmarc.{domain}", "TXT")) if t.lower().startswith("v=dmarc1")]
    if not dmarc:
        r.problem("dns:dmarc", f"_dmarc.{domain} has no DMARC record")
    else:
        pol = re.search(r"\bp=(\w+)", dmarc[0])
        r.ok("dns:dmarc", f"_dmarc.{domain} p={pol.group(1) if pol else '?'}")
    return r


def check_blocklists(q, ips: list[str], domain: str, r: Report | None = None) -> Report:
    r = r or Report()

    def lookup(name: str) -> list[str]:
        return [x for x in q(name, "A") if x.startswith("127.")]

    for ip in ips:
        addr = ipaddress.ip_address(ip)
        rev = addr.reverse_pointer.rsplit(".in-addr.arpa", 1)[0].rsplit(".ip6.arpa", 1)[0]
        for zone, label in IP_LISTS:
            if addr.version == 6 and zone != "zen.spamhaus.org":
                continue   # most lists are IPv4 only
            try:
                ans = lookup(f"{rev}.{zone}")
            except (OSError, subprocess.TimeoutExpired):
                r.note(f"bl:{zone}", f"{label}: no answer for {ip}")
                continue
            if any(a in REFUSED for a in ans):
                r.note(f"bl:{zone}", f"{label}: refused the query (use a resolver of your own, RESOLVER=...)")
            elif ans:
                r.problem(f"bl:{zone}:{ip}", f"{ip} is listed on {label} ({', '.join(ans)})")
            else:
                r.ok(f"bl:{zone}:{ip}", f"{ip} not on {label}")
    for zone, label in DOMAIN_LISTS:
        try:
            ans = lookup(f"{domain}.{zone}")
        except (OSError, subprocess.TimeoutExpired):
            r.note(f"bl:{zone}", f"{label}: no answer for {domain}")
            continue
        if any(a in REFUSED for a in ans):
            r.note(f"bl:{zone}", f"{label}: refused the query (use a resolver of your own, RESOLVER=...)")
        elif ans:
            r.problem(f"bl:{zone}:{domain}", f"{domain} is listed on {label} ({', '.join(ans)})")
        else:
            r.ok(f"bl:{zone}:{domain}", f"{domain} not on {label}")
    return r


# ---- delivery probe ------------------------------------------------------------

def send_probe(host: str, port: int, user: str, password: str, to: str, *, connect_to: str = "",
               tls: ssl.SSLContext | None = None) -> str:
    """Send one probe through submission; returns its Message-ID (without <>)."""
    mid = f"probe-{uuid.uuid4().hex}@{user.rpartition('@')[2] or host}"
    msg = EmailMessage()
    msg["From"], msg["To"] = user, to
    msg["Subject"] = f"Delivery probe {time.strftime('%Y-%m-%d', time.gmtime())}"
    msg["Date"], msg["Message-ID"] = formatdate(usegmt=True), f"<{mid}>"
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content("Daily delivery probe from the mail server. Nothing to do; it can be deleted.\n")
    with _SMTP(host, port, connect_to) as s:   # the certificate is checked against host
        s.starttls(context=tls or ssl.create_default_context())
        s.login(user, password)
        s.send_message(msg)
    return mid


class _SMTP(smtplib.SMTP):
    def __init__(self, host: str, port: int, connect_to: str):
        self._connect_to = connect_to
        super().__init__(host, port, timeout=30)

    def _get_socket(self, host, port, timeout):
        import socket
        return socket.create_connection((self._connect_to or host, port), timeout)


def probe_outcome(log_lines: list[str], message_id: str) -> tuple[str, str]:
    """('sent'|'deferred'|'bounced'|'pending', detail) for the message, from Postfix's log."""
    qid_re = r"([0-9A-F]{6,}|[0-9B-DF-HJ-NP-TV-Zb-df-hj-np-tv-z]{10,})"   # as broker/broker/trace.py
    mid = re.compile(rf"\b{qid_re}: message-id=<?{re.escape(message_id)}>?")
    qids = {m.group(1) for line in log_lines for m in [mid.search(line)] if m}
    final = None
    for line in log_lines:
        for qid in qids:
            m = re.search(rf"\b{qid}: to=<[^>]*>,.*?relay=([^,]+),.*?status=(\w+)(?: \((.*)\))?", line)
            if m and not m.group(1).startswith("dovecot"):
                final = (m.group(2), (m.group(3) or "")[:200])
    if final is None:
        return "pending", "no delivery attempt in the log yet"
    return {"sent": "sent", "deferred": "deferred", "bounced": "bounced"}.get(final[0], final[0]), final[1]


def postfix_logs(mailcow_dir: str, since: str = "15m") -> list[str]:
    out = subprocess.run(["docker", "compose", "logs", "--no-color", "--since", since, "postfix-mailcow"],
                         cwd=mailcow_dir, capture_output=True, text=True, timeout=60)
    return out.stdout.splitlines()


def check_probe(send, logs, *, wait: float = 300, step: float = 15, sleep=time.sleep,
                r: Report | None = None, to: str = "") -> Report:
    r = r or Report()
    try:
        mid = send()
    except (OSError, smtplib.SMTPException) as e:
        r.problem("probe:send", f"could not submit the probe: {type(e).__name__}: {e}")
        return r
    waited = 0.0
    while True:
        state, detail = probe_outcome(logs(), mid)
        if state != "pending" or waited >= wait:
            break
        sleep(step)
        waited += step
    if state == "sent":
        r.ok("probe", f"probe to {to} accepted by the receiving server ({detail})")
    elif state == "pending":
        r.problem("probe", f"probe to {to} not delivered after {int(wait)} s (check the queue)")
    else:
        r.problem("probe", f"probe to {to} {state}: {detail}")
    return r


# ---- command line ----------------------------------------------------------------

def _read_dkim(mailcow_dir: str, domain: str) -> str:
    """mailcow keeps DKIM public keys in Redis (hash DKIM_PUB_KEYS). Empty if not readable."""
    conf = {}
    try:
        for line in open(os.path.join(mailcow_dir, "mailcow.conf"), encoding="utf-8"):
            k, _, v = line.strip().partition("=")
            conf[k] = v
        # The password goes through the environment, never the command line (ps).
        cmd = ["docker", "compose", "exec", "-T", "-e", "REDISCLI_AUTH", "redis-mailcow", "redis-cli",
               "HGET", "DKIM_PUB_KEYS", domain]
        out = subprocess.run(cmd, cwd=mailcow_dir, capture_output=True, text=True, timeout=30,
                             env={**os.environ, "REDISCLI_AUTH": conf.get("REDISPASS", "")})
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main(env=os.environ) -> int:
    domain, host = env.get("MAIL_DOMAIN", "").strip().lower(), env.get("MAIL_HOST", "").strip().lower()
    if not domain or not host:
        print("PROBLEM\tconfig\tMAIL_DOMAIN and MAIL_HOST are needed")
        return 2
    mailcow_dir = env.get("MAILCOW_DIR", "/opt/mailcow-dockerized")
    q = dig(env.get("RESOLVER", "").strip())
    r = Report()
    try:
        check_dns(q, domain, host, env.get("PUBLIC_IPV4", "").strip(), env.get("PUBLIC_IPV6", "").strip(),
                  env.get("DKIM_SELECTOR", "dkim").strip() or "dkim", _read_dkim(mailcow_dir, domain), r)
        ips = [x for x in (env.get("PUBLIC_IPV4", "").strip(), env.get("PUBLIC_IPV6", "").strip()) if x] or \
            [x for x in q(host, "A") if re.fullmatch(r"[\d.]+", x)]
        check_blocklists(q, ips, domain, r)
    except (OSError, subprocess.TimeoutExpired) as e:
        r.problem("dns:resolver", f"DNS lookups failed: {e}")
    to = env.get("PROBE_TO", "").strip()
    if to:
        def send():
            return send_probe(host, int(env.get("PROBE_PORT", "587")), env.get("PROBE_USER", ""),
                              env.get("PROBE_PASSWORD", ""), to, connect_to=env.get("PROBE_CONNECT", "").strip())
        check_probe(send, lambda: postfix_logs(mailcow_dir), r=r, to=to)
    else:
        r.note("probe", "no delivery probe (PROBE_TO unset)")
    for status, key, text in r.lines:
        print(f"{status}\t{key}\t{text}")
    return 1 if r.problems else 0


if __name__ == "__main__":
    sys.exit(main())
