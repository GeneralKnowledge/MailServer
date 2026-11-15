"""PR 13: /v1/health feeds health-check.sh through broker.probe."""

import sqlite3

import httpx
from conftest import MON_TOKEN
from fastapi.testclient import TestClient

from broker import main as broker_main
from broker.adapters import Health
from broker.main import create_app
from broker.probe import run as probe


def lines_of(client, token):
    out: list[str] = []
    rc = probe(client, token, out=out.append)
    return rc, [line.split("\t") for line in out]


def via(ctx):
    # The probe calls 127.0.0.1:8100; route it to the in-process app.
    return TestClient(ctx.app, base_url="http://127.0.0.1:8100")


def test_probe_all_ok(ctx):
    rc, lines = lines_of(via(ctx), MON_TOKEN)
    assert rc == 0
    assert [(c, s) for c, s, _ in lines] == [("control_plane_db", "ok"), ("mailcow", "ok")]


def test_probe_reports_mailcow_down(ctx):
    ctx.fake.down = True
    rc, lines = lines_of(via(ctx), MON_TOKEN)
    assert rc == 1 and ("mailcow", "down") in [(c, s) for c, s, _ in lines]
    assert all("\n" not in d for _, _, d in lines)


def test_slow_mailcow_is_degraded(ctx, monkeypatch):
    monkeypatch.setattr(ctx.mc.__class__, "health", lambda self: Health("ok", "mailcow 2026-11", 9000))
    h = via(ctx).get("/v1/health", headers={"Authorization": f"Bearer {MON_TOKEN}"}).json()
    assert h["status"] == "degraded" and h["components"]["mailcow"]["status"] == "degraded"
    assert "slow: 9000 ms" in h["components"]["mailcow"]["detail"]
    assert broker_main.SLOW_UPSTREAM_MS == 5000


def test_database_that_cannot_be_written_is_down(ctx, tmp_path):
    path = tmp_path / "cp.sqlite3"
    from broker.store import Store
    store = Store(str(path), clock=ctx.clock)
    app = create_app(ctx.settings, mailcow=ctx.mc, store=store, clock=ctx.clock)
    blocker = sqlite3.connect(str(path), isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")           # someone else holds the write lock
    store.db.execute("PRAGMA busy_timeout=0")
    try:
        h = TestClient(app).get("/v1/health", headers={"Authorization": f"Bearer {MON_TOKEN}"}).json()
    finally:
        blocker.execute("ROLLBACK")
    assert h["status"] == "down" and h["components"]["control_plane_db"]["status"] == "down"


def test_probe_without_token_or_with_the_wrong_one(ctx):
    rc, lines = lines_of(via(ctx), "")
    assert rc == 2 and lines[0][0] == "probe" and "MONITORING_TOKEN" in lines[0][2]
    rc, lines = lines_of(via(ctx), "x" * 40)
    assert rc == 2 and "HTTP 401" in lines[0][2]


def test_probe_signup_token_is_refused(ctx):
    from conftest import TOKEN
    rc, lines = lines_of(via(ctx), TOKEN)
    assert rc == 2 and "HTTP 403" in lines[0][2]


def test_probe_when_nothing_answers():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)
    rc, lines = lines_of(httpx.Client(transport=httpx.MockTransport(handler)), "m" * 40)
    assert rc == 2 and "not answering" in lines[0][2]


def test_health_is_not_audited(ctx):
    via(ctx).get("/v1/health", headers={"Authorization": f"Bearer {MON_TOKEN}"})
    assert ctx.store.events() == []
