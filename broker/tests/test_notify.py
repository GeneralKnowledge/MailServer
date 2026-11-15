"""PR 26: sign-in notifications (broker.notify) against the Keycloak fake, and
the mailer against a real SMTP server with STARTTLS (aiosmtpd)."""

import datetime
import smtplib
import socket
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from broker import notify
from broker.config import load_settings
from broker.keycloak import KeycloakClient
from broker.store import Store
from fake_keycloak import CLIENT_ID, CLIENT_SECRET, REALM, FakeKeycloak

NOW = 1_800_000_000.0
URL = "https://mx.example.com/account/"


class Mailbox:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, to, subject, body):
        if self.fail:
            raise smtplib.SMTPServerDisconnected("connection lost")
        self.sent.append((to, subject, body))


@pytest.fixture
def n():
    class N:
        pass
    c = N()
    c.fake = FakeKeycloak()
    c.kc = KeycloakClient("http://keycloak:8080/auth", REALM, CLIENT_ID, CLIENT_SECRET, transport=c.fake.transport())
    c.store = Store(":memory:", clock=lambda: NOW)
    c.mail = Mailbox()
    c.store.link_set("alice@example.com", "keycloak", "u-alice")
    c.store.link_set("bob@example.com", "keycloak", "u-bob")
    c.out = []

    def login(user, ip, at, client="mailcow"):
        c.fake.events.append({"time": int(at * 1000), "type": "LOGIN", "userId": user, "ipAddress": ip,
                              "clientId": client})

    def run(now=NOW, **kw):
        return notify.run(c.kc, c.store, c.mail, now=now, account_url=URL, out=c.out.append, **kw)
    c.login, c.run = login, run
    return c


def test_place_is_the_network_not_the_address():
    assert notify.place_of("203.0.113.7") == notify.place_of("203.0.113.200") == "203.0.113.0/24"
    assert notify.place_of("2001:db8:1:2::5") == "2001:db8:1::/48"
    assert notify.place_of("::ffff:198.51.100.9") == "198.51.100.0/24"
    assert notify.place_of("") == notify.place_of("garbage") == "unknown"


def test_first_run_learns_and_mails_nobody(n):
    n.login("u-alice", "203.0.113.7", NOW - 86400)
    n.login("u-alice", "198.51.100.1", NOW - 3600)
    assert n.run() == 0
    assert n.mail.sent == []
    assert n.store.places("u-alice") == {"203.0.113.0/24", "198.51.100.0/24"}
    assert n.store.latest("notify.run")["counts"]["learnt"] == 2


def test_new_network_is_mailed_once_known_one_is_not(n):
    n.login("u-alice", "203.0.113.7", NOW - 3600)
    n.run()
    n.login("u-alice", "203.0.113.99", NOW + 60)      # same /24: nothing
    n.login("u-alice", "192.0.2.10", NOW + 120)       # new network
    n.login("u-alice", "192.0.2.11", NOW + 180)       # same new network again
    assert n.run(now=NOW + 600) == 0
    assert len(n.mail.sent) == 1
    to, subject, body = n.mail.sent[0]
    assert to == "alice@example.com" and "New sign-in" in subject
    assert "192.0.2.10" in body and "webmail" in body and URL in body
    ev = n.store.latest("notify.signin")
    assert ev["subject"] == {"address": "alice@example.com"} and ev["adapter"] == "smtp"
    assert "192.0.2" not in str(ev)    # the network is in the mail, not the audit log
    # A later run doesn't mail again for the same event.
    assert n.run(now=NOW + 1200) == 0 and len(n.mail.sent) == 1


def test_a_persons_first_sign_in_is_learnt_not_mailed(n):
    n.run()                                   # learning run, no events
    n.login("u-bob", "198.51.100.1", NOW + 60)   # Bob just signed up
    n.run(now=NOW + 600)
    assert n.mail.sent == [] and n.store.places("u-bob") == {"198.51.100.0/24"}
    n.login("u-bob", "192.0.2.1", NOW + 700)
    n.run(now=NOW + 1200)
    assert [m[0] for m in n.mail.sent] == ["bob@example.com"]


def test_identity_without_a_mailbox_is_skipped(n):
    n.run()
    n.login("u-stranger", "192.0.2.1", NOW + 60)
    n.run(now=NOW + 600)
    assert n.mail.sent == [] and n.store.latest("notify.run")["counts"]["skipped"] == 1


def test_failed_send_stops_and_the_next_run_retries(n):
    n.login("u-alice", "203.0.113.7", NOW - 3600)
    n.run()
    n.login("u-alice", "192.0.2.10", NOW + 60)
    n.mail.fail = True
    assert n.run(now=NOW + 600) == 1
    assert n.store.latest("notify.signin", "failed")["subject"]["address"] == "alice@example.com"
    assert "192.0.2.0/24" not in n.store.places("u-alice")
    n.mail.fail = False
    assert n.run(now=NOW + 1200) == 0
    assert len(n.mail.sent) == 1


def test_burst_of_new_networks_is_capped(n):
    n.login("u-alice", "203.0.113.7", NOW - 3600)
    n.run()
    for i in range(6):
        n.login("u-alice", f"10.{i}.0.1", NOW + 60 + i)
    n.run(now=NOW + 600)
    assert len(n.mail.sent) == notify.MAX_PER_PERSON_PER_RUN
    assert n.store.latest("notify.run")["counts"]["skipped"] == 6 - notify.MAX_PER_PERSON_PER_RUN


def test_dry_run_sends_and_records_nothing(n):
    n.login("u-alice", "203.0.113.7", NOW - 3600)
    n.run()
    n.login("u-alice", "192.0.2.10", NOW + 60)
    assert n.run(now=NOW + 600, dry_run=True) == 0
    assert n.mail.sent == [] and any("would mail alice@example.com" in line for line in n.out)
    assert "192.0.2.0/24" not in n.store.places("u-alice")


def test_events_are_paged(n):
    n.run()
    n.login("u-alice", "203.0.113.7", NOW + 1)
    for i in range(450):
        n.login("u-alice", "203.0.113.8", NOW + 2 + i)
    n.login("u-alice", "192.0.2.1", NOW + 1000)
    n.run(now=NOW + 2000)
    assert n.store.latest("notify.run")["counts"]["events"] == 452
    assert len(n.mail.sent) == 1
    assert sum(1 for m, _ in n.fake.calls if _.endswith("/events")) >= 3


def test_missing_view_events_role_is_reported(n):
    n.fake.events_forbidden = True
    assert n.run() == 2
    assert n.store.latest("notify.run", "failed")["error_kind"] == "auth"


def test_deleting_an_account_forgets_its_networks(n):
    n.login("u-alice", "203.0.113.7", NOW - 3600)
    n.run()
    n.store.places_forget("u-alice")
    assert n.store.places("u-alice") == set()


def test_old_networks_are_forgotten():
    t = [NOW]
    s = Store(":memory:", clock=lambda: t[0])
    s.place_seen("u", "203.0.113.0/24", NOW)
    t[0] = NOW + 181 * 86400
    s.prune_places()
    assert s.places("u") == set()


# ---- settings -----------------------------------------------------------------

def test_notifications_need_keycloak_and_mail_settings(env):
    load_settings(env)   # off by default
    with pytest.raises(RuntimeError, match="IDENTITY_PROVIDER=keycloak"):
        load_settings({**env, "NOTIFY_SIGNINS": "true"})
    kc = {**env, "IDENTITY_PROVIDER": "keycloak", "KEYCLOAK_CLIENT_SECRET": "s" * 40, "NOTIFY_SIGNINS": "true"}
    with pytest.raises(RuntimeError, match="NOTIFY_SMTP_HOST"):
        load_settings(kc)
    full = {**kc, "NOTIFY_SMTP_HOST": "mx.example.com", "NOTIFY_SMTP_USER": "noreply@example.com",
            "NOTIFY_SMTP_PASSWORD": "p" * 20, "NOTIFY_ACCOUNT_URL": "http://mx.example.com/account/"}
    with pytest.raises(RuntimeError, match="https"):
        load_settings(full)
    s = load_settings({**full, "NOTIFY_ACCOUNT_URL": URL, "NOTIFY_SMTP_CONNECT": "172.30.240.1"})
    assert s.notify_signins and s.notify_smtp_port == 587 and s.notify_smtp_connect == "172.30.240.1"


# ---- the mailer against a real SMTP server with STARTTLS ------------------------

def _cert(tmp_path, name):
    key = ec.generate_private_key(ec.SECP256R1())
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subj).issuer_name(subj).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cp, kp = tmp_path / f"{name}.pem", tmp_path / f"{name}.key"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    return cp, kp


@pytest.fixture
def smtp_server(tmp_path):
    aiosmtpd = pytest.importorskip("aiosmtpd.controller")
    from aiosmtpd.smtp import AuthResult, LoginPassword

    cp, kp = _cert(tmp_path, "mx.example.com")
    server_tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_tls.load_cert_chain(cp, kp)
    got = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            got.append((envelope.mail_from, envelope.rcpt_tos, envelope.content.decode(), session.authenticated))
            return "250 OK"

    def auth(server, session, envelope, mechanism, data):
        ok = isinstance(data, LoginPassword) and data.login == b"noreply@example.com" and data.password == b"pw-123"
        return AuthResult(success=ok)

    with socket.socket() as probe:   # a free port (this aiosmtpd can't take port 0)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    ctl = aiosmtpd.Controller(Handler(), hostname="127.0.0.1", port=port, tls_context=server_tls,
                              require_starttls=True, authenticator=auth, auth_require_tls=True)
    ctl.start()
    yield {"port": port, "cert": cp, "got": got}
    ctl.stop()


def test_mailer_uses_starttls_auth_and_checks_the_certificate(smtp_server):
    trust = ssl.create_default_context(cafile=str(smtp_server["cert"]))
    m = notify.Mailer("mx.example.com", "noreply@example.com", "pw-123", port=smtp_server["port"],
                      connect_to="127.0.0.1", tls=trust)
    m.send("alice@example.com", "New sign-in to your Avedeus Mail account", "hello")
    frm, to, content, authed = smtp_server["got"][0]
    assert frm == "noreply@example.com" and to == ["alice@example.com"] and authed
    assert "Auto-Submitted: auto-generated" in content and "Avedeus Mail <noreply@example.com>" in content


def test_mailer_refuses_a_certificate_for_another_name(smtp_server):
    trust = ssl.create_default_context(cafile=str(smtp_server["cert"]))
    m = notify.Mailer("other.example.com", "noreply@example.com", "pw-123", port=smtp_server["port"],
                      connect_to="127.0.0.1", tls=trust)
    with pytest.raises(ssl.SSLCertVerificationError):
        m.send("alice@example.com", "x", "y")
    assert smtp_server["got"] == []


def test_mail_text_names_the_app_and_never_a_password():
    _, body = notify.message("a@example.com", {"time_ms": int(NOW * 1000), "ip": "192.0.2.1",
                                               "client_id": "avedeus-portal"}, URL)
    assert "your account page" in body and "192.0.2.1" in body
    assert "password:" not in body.lower()
