"""The broker offers only check and create, and enforces its rules itself."""

import pytest
from conftest import AUTH, MON_AUTH, TOKEN, mailbox
from fastapi.testclient import TestClient

from broker.config import load_settings
from broker.main import create_app


def test_create_and_check(ctx):
    r = ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH)
    assert r.status_code == 201 and r.json()["address"] == "alice@example.com"
    assert ctx.fake.mailboxes["alice@example.com"]["quota"] == 2048 * 1048576
    c = ctx.client.post("/v1/addresses/check", json={"local_part": "alice"}, headers=AUTH).json()
    assert c == {"ok": True, "valid": True, "reason": None, "taken": True, "mailbox": True}
    c = ctx.client.post("/v1/addresses/check", json={"local_part": "bob"}, headers=AUTH).json()
    assert c["taken"] is False


def test_token_and_client_required(ctx, env):
    assert ctx.client.post("/v1/mailboxes", json=mailbox()).status_code == 401
    bad = {"Authorization": "Bearer " + "x" * 40}
    assert ctx.client.post("/v1/mailboxes", json=mailbox(), headers=bad).status_code == 401
    assert ctx.client.get("/v1/upstream", headers=bad).status_code == 401
    assert not ctx.fake.mailboxes
    # Only the listed client addresses (TestClient's host isn't an IP, so it's refused)
    locked = TestClient(create_app(load_settings({**env, "SIGNUP_CLIENTS": "172.30.240.10"})))
    assert locked.post("/v1/mailboxes", json=mailbox(), headers=AUTH).status_code == 403
    assert locked.get("/healthz").status_code == 200  # liveness stays open for the container health check


def test_only_check_and_create_exist(ctx):
    for method, path in [("GET", "/v1/mailboxes"), ("DELETE", "/v1/mailboxes"), ("POST", "/v1/aliases"),
                         ("POST", "/api/v1/edit/mailbox"), ("POST", "/v1/mailboxes/alice/password"),
                         ("GET", "/docs"), ("GET", "/openapi.json")]:
        assert ctx.client.request(method, path, headers=AUTH).status_code in (404, 405), path


@pytest.mark.parametrize("body,kind", [
    (mailbox("postmaster"), "invalid"),
    (mailbox("Post.Master"), "invalid"),
    (mailbox("evil@other.com"), "invalid"),
    (mailbox("a"), "invalid"),
    (mailbox("x" * 40), "invalid"),
    (mailbox(quota_mb=999999), "invalid"),
    (mailbox(quota_mb=0), "invalid"),
])
def test_rules_enforced_whatever_the_caller_says(ctx, body, kind):
    r = ctx.client.post("/v1/mailboxes", json=body, headers=AUTH)
    assert r.status_code == 422 and r.json()["kind"] == kind
    assert not ctx.fake.mailboxes


def test_domain_cannot_be_chosen(ctx):
    # Unknown fields are refused (the contract says additionalProperties: false).
    r = ctx.client.post("/v1/mailboxes", json={**mailbox(), "domain": "other.com"}, headers=AUTH)
    assert r.status_code == 422 and r.json()["kind"] == "invalid" and not ctx.fake.mailboxes


def test_taken_and_alias(ctx):
    ctx.fake.aliases.add("bob@example.com")
    assert ctx.client.post("/v1/mailboxes", json=mailbox("bob"), headers=AUTH).json()["kind"] == "taken"
    assert ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH).status_code == 201
    r = ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH)
    assert r.status_code == 409 and r.json()["kind"] == "taken"


def test_daily_limit(ctx):
    for name in ["aaa", "bbb", "ccc"]:
        assert ctx.client.post("/v1/mailboxes", json=mailbox(name), headers=AUTH).status_code == 201
    r = ctx.client.post("/v1/mailboxes", json=mailbox("ddd"), headers=AUTH)
    assert r.status_code == 429 and r.json()["kind"] == "limit" and "ddd@example.com" not in ctx.fake.mailboxes
    ctx.clock.t += 86401
    assert ctx.client.post("/v1/mailboxes", json=mailbox("ddd"), headers=AUTH).status_code == 201


def test_failures_dont_count_against_limit(ctx):
    for _ in range(5):
        ctx.fake.fail_next_add = "danger"
        assert ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH).status_code == 502
    assert ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH).status_code == 201


def test_lost_reply_is_confirmed(ctx):
    ctx.fake.fail_next_add = "timeout_after_create"
    r = ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH)
    assert r.status_code == 201


def test_upstream_errors_pass_kind(ctx):
    ctx.fake.down = True
    r = ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH)
    assert r.status_code == 502 and r.json()["kind"] == "unavailable"
    assert ctx.client.get("/v1/upstream", headers=MON_AUTH).json()["kind"] == "unavailable"


def test_upstream_check(ctx):
    r = ctx.client.get("/v1/upstream", headers=MON_AUTH)
    assert r.status_code == 200 and r.json()["mailcow_version"]


def test_display_name_cleaned(ctx):
    ctx.client.post("/v1/mailboxes", json=mailbox(name="Ali\x00ce\r\nBcc: x"), headers=AUTH)
    assert ctx.fake.mailboxes["alice@example.com"]["name"] == "Alice Bcc: x"


def test_settings_checks(env):
    with pytest.raises(RuntimeError, match="SIGNUP_TOKEN"):
        load_settings({**env, "SIGNUP_TOKEN": "short"})
    with pytest.raises(RuntimeError, match="SIGNUP_CLIENTS"):
        load_settings({**env, "SIGNUP_CLIENTS": ""})
    with pytest.raises(RuntimeError, match="renamed to SIGNUP_TOKEN"):
        load_settings({**env, "BROKER_TOKEN": "x" * 40})
    with pytest.raises(RuntimeError, match="own token"):
        load_settings({**env, "MONITORING_TOKEN": env["SIGNUP_TOKEN"]})
    with pytest.raises(RuntimeError, match="MONITORING_CLIENTS"):
        load_settings({**env, "MONITORING_CLIENTS": ""})
    # Monitoring is optional: without its token it simply doesn't exist.
    names = [c.name for c in load_settings({k: v for k, v in env.items() if not k.startswith("MONITORING")}).callers]
    assert names == ["signup"]
    assert TOKEN not in repr([c.clients for c in load_settings(env).callers])
