"""PR 30: delivery and DNS checks (scripts/lib/mailcheck.py) against a fake
resolver and Postfix log lines in mailcow's format."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import mailcheck as mc  # noqa: E402

DOMAIN, HOST, IP = "mail.example.org", "mx.example.org", "192.0.2.10"
KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAs1=="   # shortened; valid base64


def good_zone():
    return {
        (DOMAIN, "MX"): [f"10 {HOST}."],
        (HOST, "A"): [IP],
        (HOST, "AAAA"): [],
        ("10.2.0.192.in-addr.arpa", "PTR"): [f"{HOST}."],
        (DOMAIN, "TXT"): ['"v=spf1 mx -all"', '"google-site-verification=x"'],
        (f"dkim._domainkey.{DOMAIN}", "TXT"): ['"v=DKIM1;k=rsa;t=s;s=email;p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A" "MIIBCgKCAQEAs1=="'],
        (f"_dmarc.{DOMAIN}", "TXT"): ['"v=DMARC1; p=quarantine; rua=mailto:postmaster@mail.example.org"'],
    }


def resolver(zone):
    def q(name, rtype):
        return zone.get((name, rtype), [])
    return q


def statuses(r):
    return {k: s for s, k, _ in r.lines}


def test_a_correct_setup_passes():
    r = mc.check_dns(resolver(good_zone()), DOMAIN, HOST, IP, dkim_key=KEY)
    assert not r.problems, r.problems
    assert statuses(r) == {k: "ok" for k in ("dns:mx", "dns:a", "dns:ptr", "dns:spf", "dns:dkim", "dns:dmarc")}


@pytest.mark.parametrize("change,key,words", [
    ({(DOMAIN, "MX"): ["10 mx1.mail.ovh.net."]}, "dns:mx", "not mx.example.org"),
    ({(DOMAIN, "MX"): []}, "dns:mx", "no MX"),
    ({(HOST, "A"): ["198.51.100.1"]}, "dns:a", "not this server"),
    ({("10.2.0.192.in-addr.arpa", "PTR"): ["vps-1234.ovh.net."]}, "dns:ptr", "hosting provider"),
    ({(DOMAIN, "TXT"): ['"v=spf1 mx -all"', '"v=spf1 include:mx.ovh.com -all"']}, "dns:spf", "2 SPF records"),
    ({(DOMAIN, "TXT"): ['"v=spf1 include:mx.ovh.com ?all"']}, "dns:spf", "not -all or ~all"),
    ({(DOMAIN, "TXT"): ['"v=spf1 ip4:203.0.113.1 -all"']}, "dns:spf", "does not cover"),
    ({(DOMAIN, "TXT"): []}, "dns:spf", "no SPF"),
    ({(f"dkim._domainkey.{DOMAIN}", "TXT"): ['"v=DKIM1; p="']}, "dns:dkim", "no DKIM key"),
    ({(f"dkim._domainkey.{DOMAIN}", "TXT"): ['"v=DKIM1; p=AAAAotherkey"']}, "dns:dkim", "not the key mailcow"),
    ({(f"_dmarc.{DOMAIN}", "TXT"): []}, "dns:dmarc", "no DMARC"),
])
def test_each_dns_mistake_is_named(change, key, words):
    r = mc.check_dns(resolver({**good_zone(), **change}), DOMAIN, HOST, IP, dkim_key=KEY)
    found = [t for s, k, t in r.problems if k == key]
    assert found and words in found[0], r.lines


def test_dkim_without_mailcows_key_is_checked_for_presence_only():
    r = mc.check_dns(resolver(good_zone()), DOMAIN, HOST, IP)
    assert "not compared" in dict((k, t) for _, k, t in r.lines)["dns:dkim"]


def test_ipv6_is_checked_when_configured_and_noted_otherwise():
    zone = {**good_zone(), (HOST, "AAAA"): ["2001:db8::10"]}
    assert statuses(mc.check_dns(resolver(zone), DOMAIN, HOST, IP))["dns:aaaa"] == "note"
    assert statuses(mc.check_dns(resolver(zone), DOMAIN, HOST, IP, "2001:db8::10"))["dns:aaaa"] == "ok"
    assert statuses(mc.check_dns(resolver(zone), DOMAIN, HOST, IP, "2001:db8::99"))["dns:aaaa"] == "PROBLEM"


def test_blocklists_listed_clean_and_refused():
    zone = {("10.2.0.192.zen.spamhaus.org", "A"): ["127.255.255.254"],     # public resolver refused
            ("10.2.0.192.bl.spamcop.net", "A"): ["127.0.0.2"],              # listed
            (f"{DOMAIN}.dbl.spamhaus.org", "A"): []}
    r = mc.check_blocklists(resolver(zone), [IP], DOMAIN)
    st = statuses(r)
    assert st["bl:zen.spamhaus.org"] == "note"
    assert st[f"bl:bl.spamcop.net:{IP}"] == "PROBLEM"
    assert st[f"bl:b.barracudacentral.org:{IP}"] == "ok" and st[f"bl:dbl.spamhaus.org:{DOMAIN}"] == "ok"
    assert len(r.problems) == 1


def test_ipv6_only_checked_where_lists_support_it():
    r = mc.check_blocklists(resolver({}), ["2001:db8::10"], DOMAIN)
    assert [k for _, k, _ in r.lines if k.startswith("bl:") and "2001" in k] == ["bl:zen.spamhaus.org:2001:db8::10"]


# ---- delivery probe ------------------------------------------------------------------

MID = "probe-abc@mail.example.org"


def log(*lines):
    return [f"postfix-mailcow-1  | Nov 15 06:00:0{i} mx postfix/{l}" for i, l in enumerate(lines)]


SENT = log(f"cleanup[10]: 4F3C2A1B2C: message-id=<{MID}>",
           "qmgr[9]: 4F3C2A1B2C: from=<noreply@mail.example.org>, size=900, nrcpt=1 (queue active)",
           "smtp[11]: 4F3C2A1B2C: to=<me@gmail.com>, relay=gmail-smtp-in.l.google.com[142.250.1.27]:25, "
           "delay=1.2, delays=0.1/0/0.5/0.6, dsn=2.0.0, status=sent (250 2.0.0 OK 1700000000 x1si)")
DEFERRED = log(f"cleanup[10]: 4F3C2A1B2C: message-id=<{MID}>",
               "smtp[11]: 4F3C2A1B2C: to=<me@gmail.com>, relay=gmail-smtp-in.l.google.com[142.250.1.27]:25, "
               "delay=1, dsn=4.7.28, status=deferred (host said: 421-4.7.28 unusual rate of unsolicited mail)")


def test_probe_outcome_reads_postfix():
    assert mc.probe_outcome(SENT, MID)[0] == "sent"
    st, detail = mc.probe_outcome(DEFERRED, MID)
    assert st == "deferred" and "unusual rate" in detail
    assert mc.probe_outcome(SENT, "other@x")[0] == "pending"
    local = log(f"cleanup[10]: 4F3C2A1B2C: message-id=<{MID}>",
                "lmtp[12]: 4F3C2A1B2C: to=<me@mail.example.org>, relay=dovecot[172.22.1.250]:24, status=sent (ok)")
    assert mc.probe_outcome(local, MID)[0] == "pending"     # local delivery isn't the outside world


def test_check_probe_waits_then_passes_or_fails():
    calls = []
    seq = iter([[], SENT])
    r = mc.check_probe(lambda: MID, lambda: next(seq), sleep=calls.append, to="me@gmail.com")
    assert statuses(r)["probe"] == "ok" and calls == [15]
    r = mc.check_probe(lambda: MID, lambda: DEFERRED, sleep=calls.append, to="me@gmail.com")
    assert "unusual rate" in r.problems[0][2]
    r = mc.check_probe(lambda: MID, lambda: [], wait=30, sleep=lambda s: None, to="me@gmail.com")
    assert "not delivered after 30 s" in r.problems[0][2]

    def refuse():
        raise mc.smtplib.SMTPAuthenticationError(535, b"auth failed")
    assert "could not submit" in mc.check_probe(refuse, lambda: [], to="x").problems[0][2]


def test_main_needs_its_settings(capsys):
    assert mc.main({}) == 2
    assert "MAIL_DOMAIN" in capsys.readouterr().out
