import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from broker.config import load_settings  # noqa: E402
from broker.mailcow import MailcowClient  # noqa: E402
from broker.main import create_app  # noqa: E402
from fake_mailcow import API_KEY, FakeMailcow  # noqa: E402

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


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
        "BROKER_TOKEN": TOKEN,
        "BROKER_ALLOWED_CLIENTS": "*",
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
    c.app = create_app(c.settings, mailcow=mc, clock=c.clock)
    c.client = TestClient(c.app)
    return c


def mailbox(local="alice", **kw):
    body = {"local_part": local, "password": "Sunny-River-42x", "name": "Alice", "quota_mb": 2048}
    body.update(kw)
    return body
