"""Fixes from the 2026-11-15 source audit: claim ids, revoke during sign-up,
event retention, settings checks. (The admin-page fixes left with the admin
pages in PR 15; the admin console has its own tests.)"""

import pytest
from conftest import register

from app.cli import main as cli_main
from app.config import load_settings


def new_invite(c, **kw):
    kw.setdefault("quota_mb", 2048)
    kw.setdefault("days", 7)
    return c.invites.create(**kw)


# ---- M1: claims and revocation ------------------------------------------------

def test_revoke_refused_while_signup_in_progress(ctx):
    _, inv = new_invite(ctx)
    cid = ctx.invites.claim(inv.id)
    assert cid
    assert not ctx.invites.revoke(inv.id)
    ctx.invites.release(inv.id, cid)
    assert ctx.invites.revoke(inv.id)


def test_stale_claim_cannot_redeem_or_release(ctx):
    _, inv = new_invite(ctx)
    old = ctx.invites.claim(inv.id)
    ctx.clock.advance(301)  # first request hung past the claim timeout
    new = ctx.invites.claim(inv.id)
    assert new and new != old
    ctx.invites.release(inv.id, old)  # must not free the newer claim
    assert ctx.invites.get(inv.id).status == "in_progress"
    assert not ctx.invites.redeem(inv.id, old, "late@example.com")
    assert ctx.invites.redeem(inv.id, new, "alice@example.com")
    assert ctx.invites.get(inv.id).redeemed_address == "alice@example.com"


def test_revoked_mid_signup_is_flagged_not_redeemed(ctx):
    token, inv = new_invite(ctx)

    def slow_and_revoked():  # the claim times out during the mailcow call, then the admin revokes
        ctx.clock.advance(301)
        assert ctx.invites.revoke(inv.id)

    ctx.fake.on_add = slow_and_revoked
    r = register(ctx, token)
    assert r.status_code == 200  # the mailbox exists, so the user is told so
    got = ctx.invites.get(inv.id)
    assert got.status == "revoked" and got.redeemed_at is None
    detail = ctx.db.one("SELECT detail FROM events WHERE kind='provision_failed'")["detail"]
    assert "revoked or claimed again" in detail


def test_old_database_gets_claim_id_column(tmp_path):
    import sqlite3
    from app.db import Database
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE invitations (id INTEGER PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
        hint TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', quota_mb INTEGER NOT NULL, created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL, revoked_at INTEGER, claimed_at INTEGER, redeemed_at INTEGER,
        redeemed_address TEXT)""")
    con.commit(); con.close()
    db = Database(str(path))
    cols = {r["name"] for r in db.query("PRAGMA table_info(invitations)")}
    assert "claim_id" in cols


# ---- L4: retention -------------------------------------------------------------

def test_old_events_and_sessions_are_pruned(ctx):
    ctx.db.log("registered", detail="old", ip="198.51.100.7")
    ctx.clock.advance(181 * 86400)
    ctx.db.log("registered", detail="new", ip="198.51.100.8")
    assert ctx.client.get("/join/healthz").status_code == 200
    details = [r["detail"] for r in ctx.db.query("SELECT detail FROM events")]
    assert details == ["new"]


# ---- L5 / L8: settings and CLI checks -----------------------------------------

@pytest.mark.parametrize("name", ["ADMIN_USERNAME", "ADMIN_TOTP_SECRET", "ADMIN_ALLOWED_IPS"])
def test_old_admin_settings_are_refused_with_a_pointer(env, name):
    with pytest.raises(RuntimeError, match="admin pages left the sign-up app"):
        load_settings({**env, name: "leftover"})


def test_cli_create_invite_respects_max_quota(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "r.sqlite3"))
    monkeypatch.setenv("MAX_QUOTA_MB", "4096")
    assert cli_main(["create-invite", "--quota", "5000"]) == 1
    assert "MAX_QUOTA_MB" in capsys.readouterr().err
    assert cli_main(["create-invite", "--quota", "4096"]) == 0


# ---- M4 (PR 15): the internal API needs its own token, from its own address ----

def test_console_settings(env):
    s = load_settings(env)
    assert s.admin_console_clients == "172.30.240.14"
    assert load_settings({**env, "ADMIN_CONSOLE_TOKEN": ""}).admin_console_token == ""
    with pytest.raises(RuntimeError, match="at least 32"):
        load_settings({**env, "ADMIN_CONSOLE_TOKEN": "short"})
    with pytest.raises(RuntimeError, match="differ from BROKER_TOKEN"):
        load_settings({**env, "ADMIN_CONSOLE_TOKEN": env["BROKER_TOKEN"]})
    with pytest.raises(RuntimeError, match="not a valid"):
        load_settings({**env, "ADMIN_CONSOLE_CLIENTS": "my-home"})


# ---- H2: the app holds no mailcow key -----------------------------------------

def test_app_refuses_a_mailcow_key(env):
    with pytest.raises(RuntimeError, match="MAILCOW_API_KEY"):
        load_settings({**env, "MAILCOW_API_KEY": "leftover"})
    with pytest.raises(RuntimeError, match="BROKER_TOKEN"):
        load_settings({**env, "BROKER_TOKEN": ""})
    assert not any("mailcow_api" in f for f in load_settings(env).__dataclass_fields__)


def test_broker_daily_limit_surfaces_as_failure(ctx):
    from conftest import make_broker_client
    from fake_mailcow import FakeMailcow
    from app.main import create_app
    from app.security import RateLimiter
    from fastapi.testclient import TestClient
    broker = make_broker_client(FakeMailcow(), broker_env={"MAX_CREATES_PER_DAY": "1"})
    ctx.client = TestClient(create_app(ctx.settings, broker=broker, db=ctx.db,
                                       limiter=RateLimiter(clock=ctx.clock), clock=ctx.clock),
                            base_url="https://mail.example.com")
    t1, _ = new_invite(ctx)
    t2, inv2 = new_invite(ctx)
    assert register(ctx, t1, username="alice").status_code == 200
    r = register(ctx, t2, username="bobby")
    assert r.status_code == 503 and ctx.invites.get(inv2.id).status == "valid"
    assert "limit" in ctx.db.one("SELECT detail FROM events WHERE kind='provision_failed'")["detail"]
