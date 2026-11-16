"""PR 12: the sign-up flow through createAccount, with and without Keycloak.

Fakes only (mailcow and Keycloak); not proven on a server.
"""

from conftest import GOOD_PW, Clock, make_broker_client, register
from fake_keycloak import FakeKeycloak
from fake_mailcow import FakeMailcow
from fastapi.testclient import TestClient

from app.config import load_settings
from app.db import Database
from app.main import create_app
from app.security import RateLimiter


def keycloak_ctx(env):
    clock, fake, kc = Clock(), FakeMailcow(), FakeKeycloak()
    db = Database(":memory:", clock=clock)
    app = create_app(load_settings(env), broker=make_broker_client(fake, clock=clock, keycloak=kc), db=db,
                     limiter=RateLimiter(clock=clock), clock=clock)

    class Ctx:
        pass

    c = Ctx()
    c.client, c.fake, c.kc, c.invites = TestClient(app, base_url="https://mail.example.com"), fake, kc, app.state.invites
    return c


def test_with_keycloak_signup_creates_identity_then_mailbox(env):
    c = keycloak_ctx(env)
    token, inv = c.invites.create(quota_mb=2048, days=7)
    r = register(c, token)
    assert r.status_code == 200, r.text
    user = c.kc.by_username("alice")
    assert user["email"] == "alice@example.com"
    assert user["credentials"][0]["value"] == GOOD_PW
    assert c.fake.mailboxes["alice@example.com"]["_password"] != GOOD_PW
    assert "app password" in r.text.lower()
    assert c.invites.get(inv.id).status == "redeemed"


def test_with_keycloak_down_the_invitation_is_kept(env):
    c = keycloak_ctx(env)
    c.kc.down = True
    token, inv = c.invites.create(quota_mb=2048, days=7)
    r = register(c, token)
    assert r.status_code == 503
    assert not c.fake.mailboxes
    assert c.invites.get(inv.id).status == "valid"


def test_without_keycloak_the_page_keeps_the_account_password_text(ctx):
    token, _ = ctx.invites.create(quota_mb=2048, days=7)
    r = register(ctx, token)
    assert "Most apps set this up automatically" in r.text
    assert "app password, not the password" not in r.text
