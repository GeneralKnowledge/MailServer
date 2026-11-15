import base64
import json
import re
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
CP_TOKEN = "a" * 40
SIGNUP_TOKEN = "c" * 40
ADMIN_IP = "203.0.113.7"


def b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeKeycloak:
    """Token endpoint only; the browser part of the flow is simulated by the tests."""

    def __init__(self, clock):
        self.clock, self.codes, self.forms, self.issued = clock, {}, [], 0
        self.claims = {"groups": ["/admins"], "amr": ["pwd", "otp"]}

    def issue_code(self, nonce):
        code = f"code-{len(self.codes)}"
        self.codes[code] = nonce
        return code

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.forms.append(form)
        self.issued += 1
        if form["grant_type"] == "authorization_code":
            nonce = self.codes.pop(form["code"], None)
            if nonce is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
        elif form.get("refresh_token") == "refresh-1":
            nonce = None
        else:
            return httpx.Response(400, json={"error": "invalid_grant"})
        idt = "h." + b64({"iss": ISSUER, "aud": "avedeus-admin", "sub": "admin-1", "exp": self.clock() + 300,
                          "nonce": nonce, "name": "Ada Admin", **self.claims}) + ".sig"
        return httpx.Response(200, json={"access_token": f"access-{self.issued}", "expires_in": 300,
                                         "refresh_token": "refresh-1", "id_token": idt})


class FakeUpstream:
    """The control plane or the sign-up app's internal API: canned answers by path."""

    def __init__(self):
        self.calls, self.answers = [], {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, dict(request.headers), body,
                           dict(request.url.params)))
        status, data = self.answers.get((request.method, request.url.path), (404, {"ok": False, "kind": "not_found"}))
        return httpx.Response(status, json=data)


@pytest.fixture
def env():
    return {"PUBLIC_URL": "https://mx.example.com", "OIDC_ISSUER": ISSUER, "OIDC_CLIENT_SECRET": "s" * 40,
            "CONTROL_PLANE_TOKEN": CP_TOKEN, "SIGNUP_CONSOLE_TOKEN": SIGNUP_TOKEN, "SESSION_SECRET": "x" * 40,
            "ADMIN_ALLOWED_IPS": "203.0.113.0/24"}


@pytest.fixture
def ctx(env):
    class C:
        pass
    c = C()
    c.clock = Clock()
    c.kc, c.cp, c.signup = FakeKeycloak(c.clock), FakeUpstream(), FakeUpstream()
    c.settings = load_settings(env)
    oidc = OidcClient(ISSUER, "http://keycloak:8080/auth", "avedeus-admin", "s" * 40,
                      transport=httpx.MockTransport(c.kc.handle), clock=c.clock)
    c.app = create_app(c.settings, oidc=oidc, control_plane=httpx.MockTransport(c.cp.handle),
                       signup=httpx.MockTransport(c.signup.handle), clock=c.clock)
    c.client = TestClient(c.app, base_url="https://mx.example.com", follow_redirects=False,
                          client=(ADMIN_IP, 50000))
    c.signup.answers[("GET", "/internal/v1/invitations")] = (200, {
        "ok": True, "failures": [], "defaults": {"quota_mb": 2048, "max_quota_mb": 10240, "days": 7},
        "invitations": [{"id": 1, "hint": "abcdef", "note": "Sam", "quota_mb": 2048, "created_at": 1_800_000_000,
                         "expires_at": 1_800_600_000, "redeemed_address": None, "status": "valid"}]})
    return c


def sign_in(c):
    r = c.client.get("/console/login")
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    code = c.kc.issue_code(q["nonce"])
    return c.client.get("/console/callback", params={"code": code, "state": q["state"]}), q


def csrf_of(c):
    """The CSRF token lives in the server-side session and is shown in each form."""
    page = c.client.get("/console/")
    return re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
