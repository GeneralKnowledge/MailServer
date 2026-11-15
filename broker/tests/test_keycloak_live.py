"""The Keycloak adapter against a real Keycloak (PR 12).

Skipped unless KEYCLOAK_LIVE_SECRET is set. CI's "Identity stack" job starts
Keycloak from infrastructure/identity and runs this file against it, so the
adapter is checked against the real admin API, not only the fake.
"""

import json
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


def _totp(secret: bytes, at: float) -> str:
    import hashlib
    import hmac
    import struct
    mac = hmac.new(secret, struct.pack(">Q", int(at) // 30), hashlib.sha1).digest()
    o = mac[-1] & 15
    return f"{(struct.unpack('>I', mac[o:o + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


@pytest.mark.skipif(not os.environ.get("ADMIN_OIDC_SECRET"), reason="needs ADMIN_OIDC_SECRET and MAIL_HOSTNAME")
def test_live_admin_console_sign_in_needs_password_and_one_time_code():
    """PR 15 against the real Keycloak: the avedeus-admin client always asks for
    password and one-time code (a new admin sets one up first), its access
    token carries groups and amr, and the control plane accepts it for an
    admin operation. A user outside the admins group is refused."""
    import base64
    import hashlib
    import html
    import re
    import secrets
    import time
    from urllib.parse import parse_qs, urlencode, urlparse

    import httpx
    from fake_mailcow import API_KEY, FakeMailcow
    from fastapi.testclient import TestClient

    from broker.config import load_settings
    from broker.main import create_app
    from broker.mailcow import MailcowClient
    from broker.store import Store

    internal = os.environ.get("KEYCLOAK_LIVE_URL", "http://172.30.240.12:8080/auth").removesuffix("/auth")
    host = os.environ["MAIL_HOSTNAME"]
    pub, iss = f"https://{host}", f"https://{host}/auth/realms/avedeus"
    kc = KeycloakClient(internal + "/auth", "avedeus", "avedeus-control-plane", SECRET)
    s = load_settings({"MAILCOW_API_URL": "https://mail.example.com", "MAILCOW_API_KEY": API_KEY, "MAIL_DOMAIN": host,
                       "SIGNUP_TOKEN": "s" * 40, "SIGNUP_CLIENTS": "*", "CONTROL_PLANE_DB": ":memory:",
                       "IDENTITY_PROVIDER": "keycloak", "KEYCLOAK_URL": internal + "/auth",
                       "KEYCLOAK_CLIENT_SECRET": SECRET, "ADMIN_TOKEN": "a" * 40, "ADMIN_CLIENTS": "*",
                       "KEYCLOAK_ISSUER": iss})
    store = Store(":memory:")
    broker = TestClient(create_app(s, mailcow=MailcowClient(s.mailcow_api_url, API_KEY,
                                                            transport=FakeMailcow().transport()), store=store))

    def sign_in(name: str) -> tuple[httpx.Response, httpx.Client]:
        """Keycloak's own pages, as a browser would. Returns the last response."""
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q = urlencode({"client_id": "avedeus-admin", "response_type": "code", "scope": "openid profile",
                       "redirect_uri": f"{pub}/console/callback", "state": "st", "nonce": "nn",
                       "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "login"})
        b = httpx.Client(follow_redirects=False)
        page = b.get(f"{internal}/auth/realms/avedeus/protocol/openid-connect/auth?{q}")
        action = html.unescape(re.search(r'action="([^"]+)"', page.text).group(1)).replace(pub, internal)
        r = b.post(action, data={"username": name, "password": "Sunny-River-42x"})
        b.verifier = verifier
        return r, b

    def follow(b, r):
        while r.status_code in (302, 303) and not r.headers["location"].startswith(pub + "/console/callback"):
            r = b.get(r.headers["location"].replace(pub, internal))
        return r

    names = ["adm" + uuid.uuid4().hex[:6], "usr" + uuid.uuid4().hex[:6]]
    try:
        kc.create_user(username=names[0], email=f"{names[0]}@{host}", password="Sunny-River-42x", group="admins")
        kc.create_user(username=names[1], email=f"{names[1]}@{host}", password="Sunny-River-42x", group="users")
        # 1. The password alone never finishes the sign-in: a new admin must set up a one-time code.
        r, b = sign_in(names[0])
        r = follow(b, r)
        assert r.status_code == 200 and "totp" in r.text.lower(), r.text[:500]
        secret = html.unescape(re.search(r'name="totpSecret" value="([^"]+)"', r.text).group(1))
        action = html.unescape(re.search(r'action="([^"]+)"', r.text).group(1)).replace(pub, internal)
        r = b.post(action, data={"totp": _totp(secret.encode(), time.time()), "totpSecret": secret,
                                 "userLabel": "test"})
        back = follow(b, r).headers["location"]
        assert back.startswith(f"{pub}/console/callback?"), back
        def tokens(b, back):
            code = parse_qs(urlparse(back).query)["code"][0]
            tok = httpx.post(f"{internal}/auth/realms/avedeus/protocol/openid-connect/token", data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": f"{pub}/console/callback",
                "code_verifier": b.verifier, "client_id": "avedeus-admin",
                "client_secret": os.environ["ADMIN_OIDC_SECRET"]}).json()
            dec = [json.loads(base64.urlsafe_b64decode(x.split(".")[1] + "==")) for x in (tok["access_token"],
                                                                                           tok["id_token"])]
            return tok["access_token"], dec[0], dec[1]

        def admin_call(access):
            return broker.get("/v1/accounts", headers={"Authorization": "Bearer " + "a" * 40, "X-User-Token": access})

        # The sign-in that set the code up did not use it (no "otp" in amr),
        # so the control plane refuses that token: the admin signs in again.
        access, claims, _ = tokens(b, back)
        assert "otp" not in (claims.get("amr") or []) and "/admins" in claims["groups"]
        assert admin_call(access).status_code == 401
        # 2. Signing in again asks for password and code (no cookie shortcut).
        r, b2 = sign_in(names[0])
        r = follow(b2, r)
        assert r.status_code == 200 and 'name="otp"' in r.text, r.text[:500]
        action = html.unescape(re.search(r'action="([^"]+)"', r.text).group(1)).replace(pub, internal)
        time.sleep(30.5 - time.time() % 30)  # a fresh code: Keycloak refuses one used before
        r = b2.post(action, data={"otp": _totp(secret.encode(), time.time())})
        assert r.status_code in (302, 303), re.sub(r"\s+", " ", r.text)[-1500:]
        back = follow(b2, r).headers["location"]
        access, claims, idc = tokens(b2, back)
        assert "/admins" in claims["groups"] and "otp" in claims["amr"] and claims["azp"] == "avedeus-admin"
        assert "/admins" in idc["groups"] and "otp" in idc["amr"]
        # 3. The control plane verifies that token and records the admin.
        r = admin_call(access)
        assert r.status_code == 200, r.text
        # An audited admin call (the trace; no read-only key here, so it fails) records who.
        broker.get("/v1/trace", headers={"Authorization": "Bearer " + "a" * 40, "X-User-Token": access})
        assert store.events()[0]["actor"] == {"kind": "admin", "id": claims["sub"]}
        # 4. Everyone signing in to the console client needs a one-time code, admin or not
        #    (the group check then refuses non-admins: unit tests).
        r, b3 = sign_in(names[1])
        r = follow(b3, r)
        assert "totp" in r.text.lower()
    finally:
        for n in names:
            uid = kc.find_user(n)
            if uid:
                kc.delete_user(uid)
