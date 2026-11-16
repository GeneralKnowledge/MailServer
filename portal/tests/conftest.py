import base64
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.oidc import OidcClient  # noqa: E402

ISSUER = "https://mx.example.com/auth/realms/avedeus"
PORTAL_TOKEN = "p" * 40


def b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeKeycloak:
    """Token endpoint only: the browser part of the flow is simulated by the tests."""

    def __init__(self, clock):
        self.clock, self.codes, self.forms, self.issued = clock, {}, [], 0
        self.nonce_override = None

    def issue_code(self, nonce, verifier_challenge):
        code = f"code-{len(self.codes)}"
        self.codes[code] = (nonce, verifier_challenge)
        return code

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.forms.append(form)
        if form.get("client_secret") != "s" * 40:
            return httpx.Response(401, json={"error": "unauthorized_client"})
        self.issued += 1
        if form["grant_type"] == "authorization_code":
            if form["code"] not in self.codes:
                return httpx.Response(400, json={"error": "invalid_grant"})
            nonce, _ = self.codes.pop(form["code"])
        elif form["grant_type"] == "refresh_token":
            if form["refresh_token"] != "refresh-1":
                return httpx.Response(400, json={"error": "invalid_grant"})
            nonce = None
        else:
            return httpx.Response(400, json={"error": "unsupported_grant_type"})
        idt = "h." + b64({"iss": ISSUER, "aud": "avedeus-portal", "sub": "user-1", "exp": self.clock() + 300,
                          "nonce": self.nonce_override or nonce, "name": "Alice Smith",
                          "email": "alice@example.com"}) + ".sig"
        return httpx.Response(200, json={"access_token": f"access-{self.issued}", "expires_in": 300,
                                         "refresh_token": "refresh-1", "id_token": idt, "token_type": "Bearer"})


class FakeControlPlane:
    def __init__(self):
        self.calls, self.status = [], 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(dict(request.headers))
        if self.status != 200:
            return httpx.Response(self.status, json={"ok": False, "kind": "unauthorized" if self.status == 401
                                                     else "unavailable", "detail": ""})
        return httpx.Response(200, json={"ok": True, "address": "alice@example.com", "name": "Alice",
                                         "quota_mb": 2048, "used_mb": 512, "active": True})


@pytest.fixture
def env():
    return {"PUBLIC_URL": "https://mx.example.com", "OIDC_ISSUER": ISSUER, "OIDC_CLIENT_SECRET": "s" * 40,
            "PORTAL_TOKEN": PORTAL_TOKEN, "SESSION_SECRET": "x" * 40}


@pytest.fixture
def ctx(env):
    class C:
        pass
    c = C()
    c.clock = Clock()
    c.kc, c.cp = FakeKeycloak(c.clock), FakeControlPlane()
    c.settings = load_settings(env)
    oidc = OidcClient(ISSUER, "http://keycloak:8080/auth", "avedeus-portal", "s" * 40,
                      transport=httpx.MockTransport(c.kc.handle), clock=c.clock)
    c.app = create_app(c.settings, oidc=oidc, control_plane=httpx.MockTransport(c.cp.handle), clock=c.clock)
    c.client = TestClient(c.app, base_url="https://mx.example.com", follow_redirects=False)
    return c


def sign_in(c):
    r = c.client.get("/account/login")
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    code = c.kc.issue_code(q["nonce"], q["code_challenge"])
    return c.client.get("/account/callback", params={"code": code, "state": q["state"]}), q
