import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.main import create_app  # noqa: E402
from app.mailcow import MailcowClient  # noqa: E402
from app.security import RateLimiter, hash_password, new_totp_secret, totp_now  # noqa: E402

from fake_mailcow import API_KEY, FakeMailcow  # noqa: E402

ADMIN_PW = "correct horse battery staple"
TOTP = new_totp_secret()
_PW_HASH = hash_password(ADMIN_PW)
GOOD_PW = "Sunny-River-42x"


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def env():
    return {
        "MAILCOW_API_URL": "https://mail.example.com",
        "MAILCOW_API_KEY": API_KEY,
        "MAIL_DOMAIN": "example.com",
        "SESSION_SECRET": "x" * 40,
        "ADMIN_USERNAME": "boss",
        "ADMIN_PASSWORD_HASH": _PW_HASH,
        "ADMIN_TOTP_SECRET": TOTP,
        "DATABASE_PATH": ":memory:",
        "BRAND_NAME": "Test Mail",
    }


@pytest.fixture
def ctx(env):
    clock = Clock()
    settings = load_settings(env)
    fake = FakeMailcow()
    mc = MailcowClient(settings.mailcow_api_url, settings.mailcow_api_key, transport=fake.transport())
    db = Database(":memory:", clock=clock)
    app = create_app(settings, mailcow=mc, db=db, limiter=RateLimiter(clock=clock), clock=clock)
    client = TestClient(app, base_url="https://mail.example.com")

    class Ctx:
        pass

    c = Ctx()
    c.app, c.client, c.fake, c.db, c.clock, c.settings = app, client, fake, db, clock, settings
    c.invites = app.state.invites
    return c


def csrf_of(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "no csrf token in page"
    return m.group(1)


def admin_login(c):
    page = c.client.get("/join/admin/login")
    return c.client.post("/join/admin/login", data={
        "username": "boss", "password": ADMIN_PW, "code": totp_now(TOTP, at=c.clock()), "csrf": csrf_of(page.text),
    }, follow_redirects=False)


def register(c, token, username="alice", password=GOOD_PW, confirm=None, name="Alice"):
    page = c.client.get(f"/join/i/{token}")
    if 'name="csrf"' not in page.text:  # invalid invite: borrow a token from the same session
        page = c.client.get("/join/admin/login")
    csrf = csrf_of(page.text)
    return c.client.post("/join/register", data={
        "token": token, "username": username, "display_name": name, "password": password,
        "password_confirm": confirm if confirm is not None else password, "csrf": csrf,
    })
