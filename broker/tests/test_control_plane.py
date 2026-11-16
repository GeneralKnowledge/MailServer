"""PR 9: caller separation, error envelope, audit log, idempotency, health, reconciliation."""

import sqlite3

import pytest
from conftest import AUTH, MON_AUTH, mailbox
from fastapi.testclient import TestClient

from broker.config import load_settings
from broker.main import create_app
from broker.reconcile import run as reconcile

KEY = {"Idempotency-Key": "claim-0123456789abcdef"}


def actions(ctx):
    return [(e["action"], e["outcome"], e.get("error_kind")) for e in reversed(ctx.store.events())]


# ---- callers and capabilities ---------------------------------------------------

def test_each_caller_gets_only_its_own_operations(ctx):
    assert ctx.client.get("/v1/upstream", headers=AUTH).status_code == 403
    assert ctx.client.get("/v1/health", headers=AUTH).status_code == 403
    assert ctx.client.post("/v1/mailboxes", json=mailbox(), headers=MON_AUTH).status_code == 403
    assert ctx.client.post("/v1/addresses/check", json={"local_part": "x"}, headers=MON_AUTH).status_code == 403
    assert not ctx.fake.mailboxes
    assert ctx.client.get("/v1/upstream", headers=MON_AUTH).status_code == 200
    assert ctx.client.post("/v1/addresses/check", json={"local_part": "bob"}, headers=AUTH).status_code == 200


def test_authentication_comes_before_body_validation(ctx):
    # An unauthenticated caller learns nothing about the request schema.
    assert ctx.client.post("/v1/mailboxes", json={"x": 1}).status_code == 401
    assert ctx.client.post("/v1/mailboxes", content=b"{bad").status_code == 401


def test_refusals_are_audited(ctx):
    ctx.client.post("/v1/mailboxes", json=mailbox())                       # no token
    ctx.client.post("/v1/mailboxes", json=mailbox(), headers=MON_AUTH)     # wrong caller
    events = ctx.store.events()
    assert [(e["action"], e["caller"], e["error_kind"]) for e in reversed(events)] == [
        ("auth.refused", "unknown", "unauthorized"), ("auth.refused", "monitoring", "forbidden")]
    assert all(e["operation"] == "createMailbox" for e in events)


def test_disallowed_address_is_refused_per_caller(env):
    app = create_app(load_settings({**env, "MONITORING_CLIENTS": "172.30.240.99"}))
    c = TestClient(app)  # TestClient's host isn't an IP, so a real allow-list refuses it
    assert c.get("/v1/upstream", headers=MON_AUTH).json()["kind"] == "forbidden"


# ---- error envelope -------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"local_part": "alice", "password": "short", "quota_mb": 10},              # too short for the model
    {"local_part": "alice", "password": "x" * 200, "quota_mb": 10},            # too long
    {"local_part": "alice", "password": "Sunny-River-42x"},                    # missing quota
    {"local_part": "alice", "password": "Sunny-River-42x", "quota_mb": 10, "extra": 1},
])
def test_validation_errors_use_the_envelope_and_never_echo_input(ctx, body):
    r = ctx.client.post("/v1/mailboxes", json=body, headers=AUTH)
    assert r.status_code == 422
    data = r.json()
    assert set(data) == {"ok", "kind", "detail"} and data["kind"] == "invalid"
    assert body["password"] not in r.text
    assert ("request.invalid", "refused", "invalid") in actions(ctx)


def test_unknown_routes_use_the_envelope(ctx):
    r = ctx.client.get("/v1/nothing", headers=AUTH)
    assert r.status_code == 404 and r.json()["kind"] == "not_found"


# ---- audit log ------------------------------------------------------------------

def test_creation_is_audited_without_secrets(ctx):
    ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH)
    ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH)  # taken
    events = list(reversed(ctx.store.events()))
    assert [(e["action"], e["outcome"], e["error_kind"]) for e in events] == [
        ("mailbox.create", "ok", None), ("mailbox.create", "refused", "taken")]
    assert events[0]["subject"] == {"address": "alice@example.com"} and events[0]["caller"] == "signup"
    raw = "".join(r[0] for r in ctx.store.db.execute("SELECT event FROM audit_events"))
    assert "Sunny-River-42x" not in raw and "t" * 40 not in raw


def test_audit_log_is_append_only(ctx):
    ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ctx.store.db.execute("UPDATE audit_events SET outcome='refused'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        ctx.store.db.execute("DELETE FROM audit_events")


def test_daily_limit_survives_a_restart(ctx, env):
    for name in ["aaa", "bbb", "ccc"]:
        assert ctx.client.post("/v1/mailboxes", json=mailbox(name), headers=AUTH).status_code == 201
    restarted = TestClient(create_app(ctx.settings, mailcow=ctx.mc, store=ctx.store, clock=ctx.clock))
    assert restarted.post("/v1/mailboxes", json=mailbox("ddd"), headers=AUTH).status_code == 429


# ---- idempotency ----------------------------------------------------------------

def test_repeat_with_same_key_replays_the_first_result(ctx):
    first = ctx.client.post("/v1/mailboxes", json=mailbox(), headers={**AUTH, **KEY})
    calls = len(ctx.fake.calls)
    again = ctx.client.post("/v1/mailboxes", json=mailbox(), headers={**AUTH, **KEY})
    assert first.status_code == again.status_code == 201 and again.json() == first.json()
    assert len(ctx.fake.calls) == calls  # mailcow not called again
    assert ctx.store.count("mailbox.create", "ok", 0) == 1


def test_same_key_different_request_is_a_conflict(ctx):
    ctx.client.post("/v1/mailboxes", json=mailbox(), headers={**AUTH, **KEY})
    r = ctx.client.post("/v1/mailboxes", json=mailbox("bob"), headers={**AUTH, **KEY})
    assert r.status_code == 409 and r.json()["kind"] == "conflict" and "bob@example.com" not in ctx.fake.mailboxes


def test_failed_attempt_can_be_retried_with_the_same_key(ctx):
    ctx.fake.down = True
    assert ctx.client.post("/v1/mailboxes", json=mailbox(), headers={**AUTH, **KEY}).status_code == 502
    ctx.fake.down = False
    assert ctx.client.post("/v1/mailboxes", json=mailbox(), headers={**AUTH, **KEY}).status_code == 201


def test_keys_are_per_caller_and_store_no_password(ctx):
    ctx.client.post("/v1/mailboxes", json=mailbox(), headers={**AUTH, **KEY})
    rows = ctx.store.db.execute("SELECT caller, fingerprint, body FROM idempotency").fetchall()
    assert rows[0][0] == "signup" and "Sunny-River-42x" not in "".join(map(str, rows[0]))


def test_bad_key_is_refused(ctx):
    r = ctx.client.post("/v1/mailboxes", json=mailbox(), headers={**AUTH, "Idempotency-Key": "short"})
    assert r.status_code == 422 and not ctx.fake.mailboxes


# ---- health ---------------------------------------------------------------------

def test_health_reports_each_adapter(ctx):
    h = ctx.client.get("/v1/health", headers=MON_AUTH).json()
    assert h["status"] == "ok" and h["components"]["mailcow"]["status"] == "ok"
    assert h["components"]["control_plane_db"]["status"] == "ok"
    ctx.fake.down = True
    h = ctx.client.get("/v1/health", headers=MON_AUTH).json()
    assert h["status"] == "down" and h["components"]["mailcow"]["status"] == "down"


# ---- reconciliation -------------------------------------------------------------

def test_reconcile_reports_drift_and_unmanaged(ctx):
    ctx.client.post("/v1/mailboxes", json=mailbox(), headers=AUTH)
    ctx.client.post("/v1/mailboxes", json=mailbox("bob"), headers=AUTH)
    lines: list[str] = []
    assert reconcile(ctx.mc, ctx.store, "example.com", out=lines.append) == 0
    ctx.fake.mailboxes["postmaster@example.com"] = {"username": "postmaster@example.com"}
    ctx.fake.mailboxes["other@elsewhere.org"] = {"username": "other@elsewhere.org"}
    del ctx.fake.mailboxes["bob@example.com"]  # deleted outside the control plane
    lines.clear()
    assert reconcile(ctx.mc, ctx.store, "example.com", out=lines.append) == 1
    text = "\n".join(lines)
    assert "DRIFT missing in mailcow: bob@example.com" in text
    assert "unmanaged (not created through the control plane): postmaster@example.com" in text
    assert "elsewhere.org" not in text
    report = ctx.store.events()[0]
    assert report["action"] == "reconcile.report" and report["outcome"] == "failed"
    assert report["counts"]["missing_in_mailcow"] == 1 and report["counts"]["unmanaged_in_mailcow"] == 1
    assert ctx.fake.mailboxes.get("bob@example.com") is None  # report only, nothing fixed


def test_reconcile_when_mailcow_is_down(ctx):
    ctx.fake.down = True
    assert reconcile(ctx.mc, ctx.store, "example.com", out=lambda _: None) == 2
    assert ctx.store.events()[0]["outcome"] == "failed"
