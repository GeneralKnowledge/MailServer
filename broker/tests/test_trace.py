"""PR 34: read-only, admin-only message trace. Fakes only; not proven on a server.

Log lines follow Postfix, Rspamd and Dovecot's formats as mailcow's API
returns them ({"time", "program", "message"}); their exact shapes on the
server are an open check.
"""

import io

import httpx
import pytest
from conftest import ADMIN_USER, AUTH, MON_AUTH, Clock, StubAdminTokens
from fastapi.testclient import TestClient

from broker import trace as tracing
from broker.config import load_settings
from broker.mailcow import MailcowClient
from broker.main import create_app
from broker.store import Store
from fake_mailcow import API_KEY, RO_API_KEY, FakeMailcow

ADMIN = {"Authorization": "Bearer " + "a" * 40, **ADMIN_USER}
T0 = 1_800_000_000.0 - 3600  # an hour before the test clock


def pf(dt, msg, program="postfix/smtpd"):
    return {"time": str(int(T0 + dt)), "priority": "info", "program": program, "message": msg}


INBOUND = [
    pf(0, "4F2B31C0A2B: client=mail-out.example.net[198.51.100.7]"),
    pf(1, "4F2B31C0A2B: message-id=<abc123@example.net>", "postfix/cleanup"),
    pf(2, "4F2B31C0A2B: from=<bob@example.net>, size=2048, nrcpt=1 (queue active)", "postfix/qmgr"),
    pf(3, "4F2B31C0A2B: to=<alice@example.com>, relay=dovecot[172.22.1.250]:24, delay=0.4, "
          "delays=0.2/0/0/0.2, dsn=2.0.0, status=sent (250 2.0.0 <alice@example.com> kM2nB Saved)", "postfix/lmtp"),
    pf(4, "4F2B31C0A2B: removed", "postfix/qmgr"),
]
OUTBOUND = [
    pf(10, "9A8B7C6D5E: client=unknown[172.22.1.1], sasl_method=PLAIN, sasl_username=alice@example.com"),
    pf(11, "9A8B7C6D5E: message-id=<out-1@example.com>", "postfix/cleanup"),
    pf(12, "9A8B7C6D5E: from=<alice@example.com>, size=900, nrcpt=2 (queue active)", "postfix/qmgr"),
    pf(13, "9A8B7C6D5E: to=<carol@remote.test>, relay=mx.remote.test[203.0.113.5]:25, delay=1, delays=0/0/0.5/0.5, "
           "dsn=2.0.0, status=sent (250 2.0.0 OK queued as 12345)", "postfix/smtp"),
    pf(14, "9A8B7C6D5E: to=<dave@slow.test>, relay=mx.slow.test[203.0.113.9]:25, delay=30, delays=0/0/30/0, "
           "dsn=4.4.1, status=deferred (connect to mx.slow.test[203.0.113.9]:25: Connection timed out)", "postfix/smtp"),
]
REJECTED = [
    pf(20, "NOQUEUE: reject: RCPT from unknown[192.0.2.66]: 554 5.7.1 Service unavailable; Client host [192.0.2.66] "
           "blocked using zen.spamhaus.org; from=<spammer@bad.test> to=<alice@example.com> proto=ESMTP helo=<x>"),
    pf(21, "NOQUEUE: reject: RCPT from mail.ok.test[192.0.2.10]: 550 5.1.1 <nobody@example.com>: Recipient address "
           "rejected: User unknown in virtual mailbox table; from=<eve@ok.test> to=<nobody@example.com> proto=ESMTP helo=<ok>"),
]
SPAM = [
    pf(30, "1122334455: client=mail.spam.test[192.0.2.99]"),
    pf(31, "1122334455: message-id=<spam-9@spam.test>", "postfix/cleanup"),
    pf(32, "1122334455: milter-reject: END-OF-MESSAGE from mail.spam.test[192.0.2.99]: 5.7.1 Spam message rejected; "
           "from=<x@spam.test> to=<alice@example.com> proto=ESMTP helo=<spam>", "postfix/cleanup"),
]
RSPAMD = [
    {"unix_time": T0 + 2, "qid": "4F2B31C0A2B", "message-id": "abc123@example.net", "action": "no action",
     "score": 1.3, "required_score": 15, "subject": "SECRET SUBJECT",
     "symbols": {"DMARC_POLICY_ALLOW": {"score": -0.5}, "MIME_GOOD": {"score": -0.1}, "RCVD_COUNT_THREE": {"score": 1.9}}},
    {"unix_time": T0 + 32, "qid": "1122334455", "message-id": "spam-9@spam.test", "action": "reject", "score": 21.0,
     "symbols": {"BAYES_SPAM": {"score": 5.1}, "RBL_SPAMHAUS": {"score": 4.0}, "FUZZY_DENIED": {"score": 12.0}}},
]
DOVECOT = [{"time": str(int(T0 + 3)), "program": "dovecot",
            "message": "lmtp(alice@example.com)<123><kM2nB>: msgid=<abc123@example.net>: saved mail to Archive/2026"}]


@pytest.fixture
def tctx(env):
    class C:
        pass
    c = C()
    env = {**env, "ADMIN_TOKEN": "a" * 40, "ADMIN_CLIENTS": "*", "MAILCOW_READ_ONLY_API_KEY": RO_API_KEY}
    c.fake, c.clock = FakeMailcow(), Clock()
    c.fake.logs = {"postfix": INBOUND + OUTBOUND + REJECTED + SPAM, "rspamd-history": RSPAMD, "dovecot": DOVECOT}
    c.fake.mailq = [{"queue_name": "deferred", "queue_id": "9A8B7C6D5E", "arrival_time": T0 + 12,
                     "sender": "alice@example.com",
                     "recipients": [{"address": "dave@slow.test", "delay_reason": "connect to mx.slow.test: timed out"}]}]
    c.fake.quarantine = [{"id": 1, "qid": "777", "rcpt": "alice@example.com", "score": 9.0, "subject": "x", "created": T0}]
    s = load_settings(env)
    c.store = Store(":memory:", clock=c.clock)
    c.ro = MailcowClient(s.mailcow_api_url, s.mailcow_read_only_api_key, transport=c.fake.transport())
    rw = MailcowClient(s.mailcow_api_url, s.mailcow_api_key, transport=c.fake.transport())
    c.client = TestClient(create_app(s, mailcow=rw, mailcow_read_only=c.ro, store=c.store, clock=c.clock,
                                     admin_user_tokens=StubAdminTokens()))
    return c


def stages(msg):
    return [s["stage"] for s in msg["stages"]]


def test_inbound_delivered_to_a_folder(tctx):
    r = tctx.client.get("/v1/trace", params={"message_id": "abc123@example.net"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["found"] and len(d["messages"]) == 1 and d["rejections"] == []
    m = d["messages"][0]
    assert m["queue_id"] == "4F2B31C0A2B" and m["from"] == "bob@example.net" and m["to"] == ["alice@example.com"]
    assert stages(m) == ["accepted", "spam_check", "delivered_local"]
    assert "no action, score 1.3" in m["stages"][1]["detail"] and "RCVD_COUNT_THREE" in m["stages"][1]["detail"]
    assert "DMARC_POLICY_ALLOW" not in m["stages"][1]["detail"]   # only symbols that added score
    assert "folder Archive/2026" in m["stages"][2]["detail"]
    assert "SECRET SUBJECT" not in r.text                       # never content or subjects


def test_outbound_sent_and_deferred_and_still_queued(tctx):
    d = tctx.client.get("/v1/trace", params={"address": "alice@example.com"}, headers=ADMIN).json()
    out = next(m for m in d["messages"] if m["queue_id"] == "9A8B7C6D5E")
    assert stages(out) == ["accepted", "sent_remote", "deferred", "queued"]
    assert "mx.remote.test" in out["stages"][1]["detail"] and "250 2.0.0 OK" in out["stages"][1]["detail"]
    assert "Connection timed out" in out["stages"][2]["detail"]


def test_smtp_rejections_are_explained_by_kind(tctx):
    d = tctx.client.get("/v1/trace", params={"address": "alice@example.com"}, headers=ADMIN).json()
    assert [r["reason_kind"] for r in d["rejections"]] == ["blocklist"]
    d = tctx.client.get("/v1/trace", params={"address": "nobody@example.com"}, headers=ADMIN).json()
    assert d["rejections"][0]["reason_kind"] == "unknown_recipient"


def test_spam_rejected_after_content_check(tctx):
    d = tctx.client.get("/v1/trace", params={"message_id": "<spam-9@spam.test>"}, headers=ADMIN).json()
    m = d["messages"][0]
    assert stages(m) == ["accepted", "spam_check", "rejected_smtp"]
    assert "reject, score 21.0" in m["stages"][1]["detail"] and "FUZZY_DENIED (+12.0)" in m["stages"][1]["detail"]


def test_nothing_found_says_what_the_window_covers(tctx):
    d = tctx.client.get("/v1/trace", params={"address": "ghost@example.com", "hours": 72}, headers=ADMIN).json()
    assert d["found"] is False and d["messages"] == [] and d["rejections"] == []
    kinds = [n["stage"] for n in d["notes"]]
    assert kinds == ["never_reached", "window_short"]
    assert "retained log" in d["notes"][0]["detail"]
    assert any("does not prove" in lim for lim in d["limits"])


def test_window_excludes_older_messages(tctx):
    tctx.clock.t += 2 * 3600   # everything is now ~3 h old
    d = tctx.client.get("/v1/trace", params={"address": "alice@example.com", "hours": 1}, headers=ADMIN).json()
    assert not d["found"]


def test_trace_is_read_only_with_its_own_key(tctx):
    tctx.client.get("/v1/trace", params={"address": "alice@example.com"}, headers=ADMIN)
    assert tctx.fake.calls and all(m == "GET" for m, _ in tctx.fake.calls)
    # The read-only client really can't write: mailcow refuses it.
    from broker.mailcow import MailcowError
    with pytest.raises(MailcowError):
        tctx.ro.add_mailbox(local_part="x", domain="example.com", password="Long-Enough-1", quota_mb=1)


def test_trace_is_audited_with_counts_only(tctx):
    tctx.client.get("/v1/trace", params={"address": "alice@example.com"}, headers=ADMIN)
    ev = tctx.store.events()[0]
    assert ev["action"] == "message.trace" and ev["caller"] == "admin" and ev["outcome"] == "ok"
    assert ev["subject"] == {"address": "alice@example.com"}
    assert ev["counts"] == {"messages": 3, "rejections": 1}   # in, out, spam; one blocklist rejection


def test_only_admin_may_trace(tctx):
    assert tctx.client.get("/v1/trace", params={"address": "a@b.c"}, headers=AUTH).status_code == 403
    assert tctx.client.get("/v1/trace", params={"address": "a@b.c"}, headers=MON_AUTH).status_code == 403
    assert tctx.client.get("/v1/trace", params={"address": "a@b.c"}).status_code == 401


def test_bad_input_is_refused(tctx):
    r = tctx.client.get("/v1/trace", params={"address": "not an address"}, headers=ADMIN)
    assert r.status_code == 422
    assert tctx.client.get("/v1/trace", params={"hours": 0}, headers=ADMIN).status_code == 422


def test_without_read_only_key_the_trace_says_so(env):
    s = load_settings({**env, "ADMIN_TOKEN": "a" * 40, "ADMIN_CLIENTS": "*"})
    fake = FakeMailcow()
    app = create_app(s, mailcow=MailcowClient(s.mailcow_api_url, API_KEY, transport=fake.transport()),
                     store=Store(":memory:"), clock=Clock())
    # No identity provider either: only break-glass from inside the container works (PR 15).
    assert TestClient(app).get("/v1/trace", headers=ADMIN).status_code == 403
    r = TestClient(app, client=("127.0.0.1", 40000)).get("/v1/trace", params={"address": "a@b.c"}, headers=ADMIN)
    assert r.status_code == 502 and "MAILCOW_READ_ONLY_API_KEY" in r.json()["detail"]


def test_read_write_key_is_not_accepted_as_read_only(env):
    with pytest.raises(RuntimeError, match="read-only"):
        load_settings({**env, "MAILCOW_READ_ONLY_API_KEY": env["MAILCOW_API_KEY"]})


def test_cli_renders_plain_explanation(tctx, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "a" * 40)

    def handler(request):
        r = tctx.client.get("/v1/trace", params=dict(request.url.params), headers=ADMIN)
        return httpx.Response(r.status_code, json=r.json())
    lines = []
    rc = tracing.main(["--address", "alice@example.com"], client=httpx.Client(transport=httpx.MockTransport(handler)),
                      out=lines.append)
    text = "\n".join(lines)
    assert rc == 0
    assert "[delivered_local]" in text and "[deferred]" in text and "What this cannot tell you:" in text


def test_cli_needs_the_admin_token(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    out = io.StringIO()
    assert tracing.main(["--address", "a@b.c"], out=lambda s: out.write(s)) == 2
    assert "ADMIN_TOKEN" in out.getvalue()


def test_quarantine_is_reported_from_its_own_record():
    q = [{"id": 5, "qid": "4F2B31C0A2B", "rcpt": "alice@example.com", "score": 9.5, "subject": "hidden",
          "created": T0 + 3}]
    d = tracing.trace(postfix=INBOUND[:3], rspamd=[], dovecot=[], quarantine=q, queue=[], address=None,
                      message_id="<abc123@example.net>", since=T0 - 10, until=T0 + 100)
    m = d["messages"][0]
    assert stages(m) == ["accepted", "quarantined"]
    assert "alice@example.com" in m["stages"][1]["detail"] and "hidden" not in str(d)
