"""PR 12: one identity. createAccount makes the Keycloak user, then the mailbox.

Runs against fake_keycloak.py and fake_mailcow.py. Not proven on a server.
"""

import uuid

import pytest
from conftest import AUTH, MON_AUTH, Clock, mailbox
from fake_keycloak import CLIENT_SECRET, FakeKeycloak
from fastapi.testclient import TestClient

from broker.config import load_settings
from broker.keycloak import KeycloakClient
from broker.mailcow import MailcowClient
from broker.main import create_app
from broker.store import Store
from fake_mailcow import FakeMailcow

PASSWORD = "Sunny-River-42x-long"


def hdr():
    return {**AUTH, "Idempotency-Key": "claim-" + uuid.uuid4().hex}


def account(local="alice", **kw):
    return {**mailbox(local, password=PASSWORD), "invitation": "inv-42", **kw}


@pytest.fixture
def kctx(env):
    class Ctx:
        pass

    c = Ctx()
    env = {**env, "IDENTITY_PROVIDER": "keycloak", "KEYCLOAK_CLIENT_SECRET": CLIENT_SECRET,
           "MAILBOX_TEMPLATE": "avedeus-user"}
    c.fake, c.kc_fake, c.clock = FakeMailcow(), FakeKeycloak(), Clock()
    c.settings = load_settings(env)
    c.mc = MailcowClient(c.settings.mailcow_api_url, c.settings.mailcow_api_key, transport=c.fake.transport())
    c.kc = KeycloakClient(c.settings.keycloak_url, c.settings.keycloak_realm, c.settings.keycloak_client_id,
                          c.settings.keycloak_client_secret, transport=c.kc_fake.transport())
    c.store = Store(":memory:", clock=c.clock)
    c.client = TestClient(create_app(c.settings, mailcow=c.mc, store=c.store, keycloak=c.kc, clock=c.clock))
    return c


def test_settings_require_a_secret_for_keycloak(env):
    with pytest.raises(RuntimeError, match="KEYCLOAK_CLIENT_SECRET"):
        load_settings({**env, "IDENTITY_PROVIDER": "keycloak"})
    with pytest.raises(RuntimeError, match="none or keycloak"):
        load_settings({**env, "IDENTITY_PROVIDER": "ldap"})
    assert load_settings(env).identity_provider == "none"


def test_account_creates_identity_then_mailbox(kctx):
    r = kctx.client.post("/v1/accounts", json=account(), headers=hdr())
    assert r.status_code == 201, r.text
    assert r.json() == {"ok": True, "address": "alice@example.com", "app_password_required": True}
    user = kctx.kc_fake.by_username("alice")
    assert user["email"] == "alice@example.com" and user["enabled"] and user["emailVerified"]
    assert user["groups"] == ["/users"]
    assert user["attributes"] == {"mailcow_template": ["avedeus-user"]}
    assert user["credentials"][0] == {"type": "password", "value": PASSWORD, "temporary": False}
    mb = kctx.fake.mailboxes["alice@example.com"]
    # The user's password lives in Keycloak only; the mailbox gets a random one.
    assert mb["_password"] != PASSWORD and len(mb["_password"]) >= 40
    assert kctx.store.link_get("alice@example.com", "keycloak") == user["id"]
    ev = kctx.store.events()[0]
    assert ev["action"] == "account.create" and ev["outcome"] == "ok" and ev["adapter"] == "keycloak"
    assert ev["changes"]["invitation"] == {"from": None, "to": "inv-42"}
    assert PASSWORD not in str(kctx.store.events())


def test_identity_first_then_mailbox_with_keycloak_as_its_login(kctx, monkeypatch):
    seen = {}
    real = kctx.mc.add_mailbox

    def add(**kw):
        seen.update(kw, identity_existed=bool(kctx.kc_fake.by_username("alice")))
        return real(**kw)
    monkeypatch.setattr(kctx.mc, "add_mailbox", add)
    assert kctx.client.post("/v1/accounts", json=account(), headers=hdr()).status_code == 201
    assert seen["authsource"] == "keycloak" and seen["identity_existed"]


def test_create_mailbox_is_closed_when_keycloak_is_on(kctx):
    r = kctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH)
    assert r.status_code == 409 and r.json()["kind"] == "conflict"
    assert not kctx.fake.mailboxes and not kctx.kc_fake.users


def test_without_keycloak_create_account_is_mailcow_only(ctx):
    r = ctx.client.post("/v1/accounts", json=account(), headers=hdr())
    assert r.status_code == 201
    assert r.json()["app_password_required"] is False
    assert ctx.fake.mailboxes["alice@example.com"]["_password"] == PASSWORD
    assert ctx.store.events()[0]["adapter"] == "mailcow"


def test_password_policy_refusal_creates_nothing(kctx):
    r = kctx.client.post("/v1/accounts", json=account(password="short-but-8"), headers=hdr())
    assert r.status_code == 422 and r.json()["kind"] == "password"
    assert "short-but-8" not in r.text
    assert not kctx.kc_fake.users and not kctx.fake.mailboxes


def test_existing_identity_is_never_adopted(kctx):
    kctx.kc_fake.users["x"] = {"id": "x", "username": "alice"}
    r = kctx.client.post("/v1/accounts", json=account(), headers=hdr())
    assert r.status_code == 409 and r.json()["kind"] == "taken"
    assert not kctx.fake.mailboxes


def test_taken_mailbox_is_checked_before_identity(kctx):
    kctx.fake.aliases.add("alice@example.com")
    r = kctx.client.post("/v1/accounts", json=account(), headers=hdr())
    assert r.status_code == 409
    assert not kctx.kc_fake.users


def test_mailcow_error_keeps_identity_for_a_retry(kctx):
    kctx.fake.fail_next_add = "danger"
    r = kctx.client.post("/v1/accounts", json=account(), headers=hdr())
    assert r.status_code == 502 and r.json()["kind"] == "rejected"
    # 5xx: mailcow may have done something; keep the identity so a retry continues.
    assert kctx.kc_fake.by_username("alice")


def test_definitive_mailbox_refusal_undoes_identity(kctx, monkeypatch):
    from broker.mailcow import MailcowError

    def refuse(**kw):
        raise MailcowError("password", "password_complexity")
    monkeypatch.setattr(kctx.mc, "add_mailbox", refuse)
    r = kctx.client.post("/v1/accounts", json=account(), headers=hdr())
    assert r.status_code == 422
    assert not kctx.kc_fake.users
    assert kctx.store.link_get("alice@example.com", "keycloak") is None


def test_retry_after_lost_mailbox_reply_continues(kctx):
    kctx.fake.fail_next_add = "http500"
    key = {"Idempotency-Key": "claim-" + "a" * 20}
    r = kctx.client.post("/v1/accounts", json=account(), headers={**AUTH, **key})
    assert r.status_code == 502
    uid = kctx.kc_fake.by_username("alice")["id"]
    r = kctx.client.post("/v1/accounts", json=account(), headers={**AUTH, **key})
    assert r.status_code == 201, r.text
    assert len(kctx.kc_fake.users) == 1 and kctx.kc_fake.by_username("alice")["id"] == uid
    assert "alice@example.com" in kctx.fake.mailboxes


def test_lost_keycloak_reply_is_recognised_as_ours(kctx):
    kctx.kc_fake.fail_next_create = "timeout"
    r = kctx.client.post("/v1/accounts", json=account(), headers=hdr())
    assert r.status_code == 201, r.text
    assert len(kctx.kc_fake.users) == 1


def test_keycloak_down_creates_nothing(kctx):
    kctx.kc_fake.down = True
    r = kctx.client.post("/v1/accounts", json=account(), headers=hdr())
    assert r.status_code == 502 and r.json()["kind"] == "unavailable"
    assert not kctx.fake.mailboxes
    assert kctx.store.events()[0]["outcome"] == "failed"


def test_replay_returns_first_result(kctx):
    key = {"Idempotency-Key": "claim-" + "b" * 20}
    a = kctx.client.post("/v1/accounts", json=account(), headers={**AUTH, **key})
    b = kctx.client.post("/v1/accounts", json=account(), headers={**AUTH, **key})
    assert a.status_code == b.status_code == 201 and a.json() == b.json()
    assert len(kctx.kc_fake.users) == 1


def test_daily_cap_counts_accounts_and_mailboxes(kctx):
    for n in ("ann", "bob", "cat"):
        assert kctx.client.post("/v1/accounts", json=account(n), headers=hdr()).status_code == 201
    r = kctx.client.post("/v1/accounts", json=account("dan"), headers=hdr())
    assert r.status_code == 429


def test_invitation_reference_is_validated(kctx):
    r = kctx.client.post("/v1/accounts", json=account(invitation="has spaces/and?"), headers=hdr())
    assert r.status_code == 422
    r = kctx.client.post("/v1/accounts", json={k: v for k, v in account().items() if k != "invitation"}, headers=hdr())
    assert r.status_code == 422


def test_health_reports_keycloak(kctx):
    h = kctx.client.get("/v1/health", headers=MON_AUTH).json()
    assert h["components"]["keycloak"]["status"] == "ok"
    kctx.kc_fake.down = True
    h = kctx.client.get("/v1/health", headers=MON_AUTH).json()
    assert h["components"]["keycloak"]["status"] == "down" and h["status"] == "down"


def test_token_is_reused_until_near_expiry(kctx):
    for n in ("ann", "bob"):
        kctx.client.post("/v1/accounts", json=account(n), headers=hdr())
    assert kctx.kc_fake.issued == 1


def test_wrong_client_secret_is_an_auth_error():
    fake = FakeKeycloak()
    kc = KeycloakClient("http://keycloak:8080/auth", "avedeus", "avedeus-control-plane", "wrong" * 8,
                        transport=fake.transport())
    h = kc.health()
    assert h.status == "down" and h.detail.startswith("auth")


def test_create_account_requires_an_idempotency_key(kctx):
    r = kctx.client.post("/v1/accounts", json=account(), headers=AUTH)
    assert r.status_code == 422 and "Idempotency-Key" in r.json()["detail"]
    assert not kctx.kc_fake.users
