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
