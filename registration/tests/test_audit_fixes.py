"""Fixes from the 2026-11-15 source audit: claim ids, revoke during sign-up,
new-invite token kept out of the cookie, event retention, settings checks."""

import base64
import json
import re

import pytest
from conftest import admin_login, csrf_of, register

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


# ---- L1: new invite token not in the session cookie ---------------------------

def _cookie_payload(client) -> dict:
    raw = client.cookies.get("reg_session")
    data = raw.split(".")[0]
    return json.loads(base64.b64decode(data + "=" * (-len(data) % 4)))


def test_new_invite_token_not_stored_in_cookie(ctx):
    admin_login(ctx)
    page = ctx.client.get("/join/admin")
    r = ctx.client.post("/join/admin/invites", data={"csrf": csrf_of(page.text), "note": "x"},
                        follow_redirects=False)
    assert r.status_code == 303
    assert "new_invite" not in _cookie_payload(ctx.client)
    shown = ctx.client.get("/join/admin").text
    token = re.search(r'id="newcode" readonly value="([^"]+)"', shown).group(1)
    assert token not in json.dumps(_cookie_payload(ctx.client))
    assert "Copy it now" not in ctx.client.get("/join/admin").text  # shown once


def test_new_invite_reveal_expires(ctx):
    admin_login(ctx)
    page = ctx.client.get("/join/admin")
    ctx.client.post("/join/admin/invites", data={"csrf": csrf_of(page.text)}, follow_redirects=False)
    ctx.clock.advance(301)
    assert "Copy it now" not in ctx.client.get("/join/admin").text


# ---- L4: retention -------------------------------------------------------------

def test_old_events_and_sessions_are_pruned(ctx):
    ctx.db.log("admin_login_failed", detail="old", ip="198.51.100.7")
    ctx.clock.advance(181 * 86400)
    ctx.db.log("admin_login_failed", detail="new", ip="198.51.100.8")
    assert ctx.client.get("/join/healthz").status_code == 200
    details = [r["detail"] for r in ctx.db.query("SELECT detail FROM events")]
    assert details == ["new"]


# ---- L5 / L8: settings and CLI checks -----------------------------------------

@pytest.mark.parametrize("secret", ["not base32!", "ABCDEFGH"])
def test_bad_totp_secret_rejected_at_startup(env, secret):
    env["ADMIN_TOTP_SECRET"] = secret
    with pytest.raises(RuntimeError, match="ADMIN_TOTP_SECRET"):
        load_settings(env)


def test_cli_create_invite_respects_max_quota(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "r.sqlite3"))
    monkeypatch.setenv("MAX_QUOTA_MB", "4096")
    assert cli_main(["create-invite", "--quota", "5000"]) == 1
    assert "MAX_QUOTA_MB" in capsys.readouterr().err
    assert cli_main(["create-invite", "--quota", "4096"]) == 0
