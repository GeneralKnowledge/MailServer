import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from broker.config import load_settings  # noqa: E402
from broker.mailcow import MailcowClient  # noqa: E402
from broker.main import create_app  # noqa: E402
from broker.store import Store  # noqa: E402
from fake_mailcow import API_KEY, FakeMailcow  # noqa: E402

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MON_TOKEN = "m" * 40
MON_AUTH = {"Authorization": f"Bearer {MON_TOKEN}"}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    return {
        "MAILCOW_API_URL": "https://mail.example.com",
        "MAILCOW_API_KEY": API_KEY,
        "MAIL_DOMAIN": "example.com",
        "SIGNUP_TOKEN": TOKEN,
        "SIGNUP_CLIENTS": "*",
        "MONITORING_TOKEN": MON_TOKEN,
        "MONITORING_CLIENTS": "*",
        "CONTROL_PLANE_DB": ":memory:",
        "MAX_QUOTA_MB": "4096",
        "MAX_CREATES_PER_DAY": "3",
    }


@pytest.fixture
def ctx(env):
    class Ctx:
        pass

    c = Ctx()
    c.fake, c.clock = FakeMailcow(), Clock()
    c.settings = load_settings(env)
    mc = MailcowClient(c.settings.mailcow_api_url, c.settings.mailcow_api_key, transport=c.fake.transport())
    c.mc = mc
    c.store = Store(":memory:", clock=c.clock)
    c.app = create_app(c.settings, mailcow=mc, store=c.store, clock=c.clock)
    c.client = TestClient(c.app)
    return c


def mailbox(local="alice", **kw):
    body = {"local_part": local, "password": "Sunny-River-42x", "name": "Alice", "quota_mb": 2048}
    body.update(kw)
    return body


class StubAdminTokens:
    """Stands in for the admin UserTokenVerifier (PR 15): the real one is tested
    in test_own_account.py and test_admin.py against signed tokens."""

    GOOD = "admin-user-token"

    def __init__(self, amr=("pwd", "otp")):
        self.amr = list(amr)

    def verify(self, raw):
        from broker.usertoken import TokenError
        if raw != self.GOOD:
            raise TokenError("bad signature")
        return {"sub": "admin-sub-1", "azp": "avedeus-admin", "groups": ["admins"], "amr": self.amr}


ADMIN_USER = {"X-User-Token": StubAdminTokens.GOOD}
