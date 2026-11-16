"""PR 15: the control plane's admin API. Every admin call carries the admin's
own verified Keycloak token (ADR 0020); invitations stay in the sign-up app.

Fakes and locally signed tokens only. Not proven on a server.
"""

import httpx
import pytest
from conftest import AUTH, MON_AUTH, Clock
from fake_keycloak import CLIENT_SECRET, FakeKeycloak
from fastapi.testclient import TestClient
from test_own_account import ISSUER, KEY, OTHER_KEY, jwks, token

from broker.config import load_settings
from broker.keycloak import KeycloakClient
from broker.mailcow import MailcowClient
from broker.main import create_app
from broker.store import Store
from broker.usertoken import UserTokenVerifier
from fake_mailcow import RO_API_KEY, FakeMailcow

ADMIN_TOKEN = "a" * 40
BEARER = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def admin_token(**over):
    claims = {"azp": "avedeus-admin", "groups": ["/admins"], "amr": ["pwd", "otp"], "sub": "admin-1"}
    claims.update(over)
    return token(**claims)


def as_admin(tok=None):
    return {**BEARER, "X-User-Token": tok if tok is not None else admin_token()}


@pytest.fixture
def actx(env):
    class C:
        pass
    c = C()
    env = {**env, "IDENTITY_PROVIDER": "keycloak", "KEYCLOAK_CLIENT_SECRET": CLIENT_SECRET,
           "ADMIN_TOKEN": ADMIN_TOKEN, "ADMIN_CLIENTS": "*", "KEYCLOAK_ISSUER": ISSUER,
           "MAILCOW_READ_ONLY_API_KEY": RO_API_KEY}
    c.fake, c.kc_fake, c.clock = FakeMailcow(), FakeKeycloak(), Clock()
    s = c.settings = load_settings(env)
    c.verifier = UserTokenVerifier("http://keycloak:8080/auth/realms/avedeus/protocol/openid-connect/certs",
                                   ISSUER, s.admin_client_id, required_group=s.admin_group,
                                   transport=httpx.MockTransport(lambda r: httpx.Response(200, json=jwks(KEY))),
                                   clock=c.clock)
    c.store = Store(":memory:", clock=c.clock)
    mc = MailcowClient(s.mailcow_api_url, s.mailcow_api_key, transport=c.fake.transport())
    ro = MailcowClient(s.mailcow_api_url, s.mailcow_read_only_api_key, transport=c.fake.transport())
    kc = KeycloakClient(s.keycloak_url, s.keycloak_realm, s.keycloak_client_id, s.keycloak_client_secret,
                        transport=c.kc_fake.transport())
    c.app = create_app(s, mailcow=mc, mailcow_read_only=ro, keycloak=kc, store=c.store,
                       admin_user_tokens=c.verifier, clock=c.clock)
    c.client = TestClient(c.app)
    return c


# ---- who may call ----------------------------------------------------------

@pytest.mark.parametrize("tok, why", [
    ("", "no user token"),
    ("garbage", "not a JWT"),
    (admin_token(azp="avedeus-portal"), "issued to the portal"),
    (admin_token(groups=["/users"]), "not in admins"),
    (admin_token(amr=["pwd"]), "no second factor"),
    (admin_token(amr=None), "no amr at all"),
    (token(key=OTHER_KEY, azp="avedeus-admin", groups=["admins"], amr=["otp"]), "wrong signature"),
])
def test_admin_calls_need_a_verified_admin_with_a_second_factor(actx, tok, why):
    r = actx.client.get("/v1/accounts", headers=as_admin(tok))
    assert r.status_code == 401, why
    assert r.json()["kind"] == "unauthorized"
    ev = actx.store.events()[0]
    assert ev["action"] == "auth.refused" and ev["operation"] == "listAccounts"
    assert tok == "" or tok not in str(actx.store.events())


def test_the_admin_token_alone_is_not_enough_but_the_user_is_recorded(actx):
    assert actx.client.get("/v1/accounts", headers=BEARER).status_code == 401
    r = actx.client.get("/v1/trace", params={"address": "a@example.com"}, headers=as_admin())
    assert r.status_code == 200, r.text
    assert actx.store.events()[0]["actor"] == {"kind": "admin", "id": "admin-1"}


def test_second_factor_check_can_be_turned_off(env):
    s = load_settings({**env, "ADMIN_REQUIRE_OTP": "false"})
    assert s.admin_require_otp is False
    assert load_settings(env).admin_require_otp is True


def test_break_glass_from_inside_the_container(actx):
    c = TestClient(actx.app, client=("127.0.0.1", 40000))
    assert c.get("/v1/accounts", headers=BEARER).status_code == 200
    c.get("/v1/trace", headers=BEARER)
    assert actx.store.events()[0]["actor"] == {"kind": "token", "id": "admin-break-glass"}
    # ... still with the admin caller's own token.
    assert c.get("/v1/accounts", headers=AUTH).status_code == 403


def test_signup_and_monitoring_cannot_use_admin_operations(actx):
    for hdr in (AUTH, MON_AUTH):
        assert actx.client.get("/v1/trace", headers=hdr).status_code == 403
        assert actx.client.get("/v1/accounts", headers=hdr).status_code == 403
        assert actx.client.get("/v1/audit", headers=hdr).status_code == 403


def test_admin_cannot_create_accounts(actx):
    r = actx.client.post("/v1/accounts", headers={**as_admin(), "Idempotency-Key": "k" * 20},
                         json={"local_part": "eve", "password": "Sunny-River-42x", "quota_mb": 10, "invitation": "x"})
    assert r.status_code == 403


# ---- accounts and audit ------------------------------------------------------

def test_list_accounts_is_read_only_and_uses_the_read_only_key(actx):
    actx.fake.mailboxes = {
        "bob@example.com": {"username": "bob@example.com", "name": "Bob", "quota": 1024 * 1048576,
                            "quota_used": 0, "active": 1, "created": "2026-11-15 10:00:00"},
        "alice@example.com": {"username": "alice@example.com", "name": "Alice", "quota": 2048 * 1048576,
                              "quota_used": 512 * 1048576, "active": 0},
        "x@other.test": {"username": "x@other.test"},
    }
    actx.store.link_set("alice@example.com", "keycloak", "u-1")
    actx.fake.calls.clear()
    r = actx.client.get("/v1/accounts", headers=as_admin())
    assert r.status_code == 200
    a = r.json()["accounts"]
    assert [x["address"] for x in a] == ["alice@example.com", "bob@example.com"]
    assert a[0] == {"address": "alice@example.com", "name": "Alice", "quota_mb": 2048, "used_mb": 512,
                    "active": False, "state": "inactive", "created": "", "identity_linked": True,
                    "deletion_due": None}
    assert a[1]["identity_linked"] is False and a[1]["created"] == "2026-11-15 10:00:00"
    assert all(m == "GET" for m, _ in actx.fake.calls)


def test_read_audit_newest_first_with_filters(actx):
    actx.client.get("/v1/accounts", headers=as_admin("bad"))
    actx.client.get("/v1/trace", headers=as_admin())
    ev = actx.client.get("/v1/audit", headers=as_admin()).json()["events"]
    assert [e["action"] for e in ev] == ["message.trace", "auth.refused"]
    ev = actx.client.get("/v1/audit", params={"action": "message"}, headers=as_admin()).json()["events"]
    assert [e["action"] for e in ev] == ["message.trace"]
    ev = actx.client.get("/v1/audit", params={"outcome": "refused"}, headers=as_admin()).json()["events"]
    assert [e["action"] for e in ev] == ["auth.refused"]
    assert actx.client.get("/v1/audit", params={"limit": 0}, headers=as_admin()).status_code == 422


def test_without_identity_provider_only_break_glass_works(env):
    s = load_settings({**env, "ADMIN_TOKEN": ADMIN_TOKEN, "ADMIN_CLIENTS": "*"})
    app = create_app(s, mailcow=MailcowClient(s.mailcow_api_url, s.mailcow_api_key,
                                              transport=FakeMailcow().transport()),
                     store=Store(":memory:"), clock=Clock())
    assert TestClient(app).get("/v1/accounts", headers=as_admin()).status_code == 403
    assert TestClient(app, client=("127.0.0.1", 1)).get("/v1/accounts", headers=BEARER).status_code == 200

