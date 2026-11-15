"""PR 9: the sign-up app's side of the control-plane contract."""

import httpx
import pytest

from app.provisioning import BrokerClient, ProvisioningError

PW = "Sunny-River-42x"


def client(handler) -> BrokerClient:
    return BrokerClient("http://broker", "b" * 40, transport=httpx.MockTransport(handler))


def test_a_non_envelope_422_is_not_kept(caplog):
    # A reply that echoes our request (as FastAPI's default does) must not reach our event log.
    def handler(request):
        return httpx.Response(422, json={"detail": [{"loc": ["body", "password"], "input": {"password": PW}}]})

    with pytest.raises(ProvisioningError) as e:
        client(handler).create_mailbox(local_part="alice", password=PW, quota_mb=10)
    assert e.value.kind == "invalid" and PW not in e.value.detail and PW not in str(e.value)


def test_create_sends_the_idempotency_key():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(201, json={"ok": True, "address": "alice@example.com"})

    client(handler).create_mailbox(local_part="alice", password=PW, quota_mb=10, idempotency_key="claim-" + "a" * 32)
    assert seen["idempotency-key"] == "claim-" + "a" * 32

