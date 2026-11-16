"""The Keycloak adapter against a real Keycloak (PR 12).

Skipped unless KEYCLOAK_LIVE_SECRET is set. CI's "Identity stack" job starts
Keycloak from infrastructure/identity and runs this file against it, so the
adapter is checked against the real admin API, not only the fake.
"""

import os
import uuid

import pytest

from broker.keycloak import KeycloakClient, KeycloakError

SECRET = os.environ.get("KEYCLOAK_LIVE_SECRET", "")
pytestmark = pytest.mark.skipif(not SECRET, reason="no live Keycloak (set KEYCLOAK_LIVE_SECRET)")


@pytest.fixture
def kc():
    return KeycloakClient(os.environ.get("KEYCLOAK_LIVE_URL", "http://172.30.240.12:8080/auth"),
                          "avedeus", "avedeus-control-plane", SECRET)


def test_live_health(kc):
    assert kc.health().status == "ok"


def test_live_create_find_refuse_and_delete(kc):
    name = "live-" + uuid.uuid4().hex[:8]
    uid = kc.create_user(username=name, email=f"{name}@example.test", password="Sunny-River-42x",
                         name="Live Test", attributes={"mailcow_template": "avedeus-user"})
    try:
        assert kc.find_user(name) == uid
        with pytest.raises(KeycloakError) as e:
            kc.create_user(username=name, email=f"other-{name}@example.test", password="Sunny-River-42x")
        assert e.value.kind == "taken"
        with pytest.raises(KeycloakError) as e:
            kc.create_user(username=name + "x", email=f"x-{name}@example.test", password="short")
        assert e.value.kind == "password" and "short" not in str(e.value)
    finally:
        kc.delete_user(uid)
    assert kc.find_user(name) is None


@pytest.mark.skipif(not os.environ.get("PORTAL_OIDC_SECRET"), reason="needs PORTAL_OIDC_SECRET and MAIL_HOSTNAME")
def test_live_sign_up_then_sign_in_through_the_portal():
    """PR 12 + PR 14 end to end against the real Keycloak: createAccount makes the
    user, a browser signs in at Keycloak's own login page, the portal exchanges
    the code, and the control plane accepts the real access token for /v1/me."""
    import html
    import re
    import sys
    from pathlib import Path
    from urllib.parse import parse_qs, urlparse

    import httpx
    from fastapi.testclient import TestClient

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "portal"))
    from app.config import load_settings as portal_settings
    from app.main import create_app as portal_app
    from fake_mailcow import API_KEY, FakeMailcow

    from broker.config import load_settings
    from broker.main import create_app
    from broker.mailcow import MailcowClient
    from broker.store import Store

    internal = os.environ.get("KEYCLOAK_LIVE_URL", "http://172.30.240.12:8080/auth").removesuffix("/auth")
    host = os.environ["MAIL_HOSTNAME"]
    pub, iss = f"https://{host}", f"https://{host}/auth/realms/avedeus"
    s = load_settings({"MAILCOW_API_URL": "https://mail.example.com", "MAILCOW_API_KEY": API_KEY, "MAIL_DOMAIN": host,
                       "SIGNUP_TOKEN": "s" * 40, "SIGNUP_CLIENTS": "*", "CONTROL_PLANE_DB": ":memory:",
                       "IDENTITY_PROVIDER": "keycloak", "KEYCLOAK_URL": internal + "/auth",
                       "KEYCLOAK_CLIENT_SECRET": SECRET, "PORTAL_TOKEN": "p" * 40, "PORTAL_CLIENTS": "*",
                       "KEYCLOAK_ISSUER": iss})
    fake = FakeMailcow()
    broker = TestClient(create_app(s, mailcow=MailcowClient(s.mailcow_api_url, API_KEY, transport=fake.transport()),
                                   store=Store(":memory:")))
    name = "e2e" + uuid.uuid4().hex[:6]
    r = broker.post("/v1/accounts", json={"local_part": name, "password": "Sunny-River-42x", "quota_mb": 1024,
                                          "invitation": "inv-live"},
                    headers={"Authorization": "Bearer " + "s" * 40, "Idempotency-Key": "claim-" + uuid.uuid4().hex})
    assert r.status_code == 201, r.text
    fake.mailboxes[f"{name}@{host}"]["quota_used"] = 100 * 1048576
    kc = KeycloakClient(internal + "/auth", "avedeus", "avedeus-control-plane", SECRET)
    try:
        portal = TestClient(portal_app(portal_settings({
            "PUBLIC_URL": pub, "OIDC_ISSUER": iss, "OIDC_CLIENT_SECRET": os.environ["PORTAL_OIDC_SECRET"],
            "PORTAL_TOKEN": "p" * 40, "SESSION_SECRET": "x" * 40, "KEYCLOAK_INTERNAL_URL": internal + "/auth"}),
            control_plane=broker._transport), base_url=pub, follow_redirects=False)
        browser = httpx.Client(follow_redirects=False)
        login = browser.get(portal.get("/account/login").headers["location"].replace(pub, internal))
        action = html.unescape(re.search(r'action="([^"]+)"', login.text).group(1)).replace(pub, internal)
        back = browser.post(action, data={"username": name, "password": "Sunny-River-42x"}).headers["location"]
        assert back.startswith(f"{pub}/account/callback?")
        q = {k: v[0] for k, v in parse_qs(urlparse(back).query).items()}
        assert portal.get("/account/callback", params={"code": q["code"], "state": q["state"]}).status_code == 303
        page = portal.get("/account/")
        assert page.status_code == 200 and f"{name}@{host}" in page.text and "100 MB of 1024 MB used" in page.text
    finally:
        uid = kc.find_user(name)
        if uid:
            kc.delete_user(uid)
