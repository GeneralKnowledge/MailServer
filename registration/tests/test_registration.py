"""Acceptance tests from the project brief, section 13 'Registration'."""

from conftest import GOOD_PW, register


def new_invite(c, **kw):
    kw.setdefault("quota_mb", 2048)
    kw.setdefault("days", 7)
    token, inv = c.invites.create(**kw)
    return token, inv


def test_valid_invitation_registers_mailbox(ctx):
    token, inv = new_invite(ctx, quota_mb=3000)
    r = register(ctx, token, username="Alice", name="Alice Smith")
    assert r.status_code == 200, r.text
    assert "alice@example.com" in r.text and "Open webmail" in r.text
    mb = ctx.fake.mailboxes["alice@example.com"]
    assert mb["quota"] == 3000 * 1048576 and mb["name"] == "Alice Smith" and mb["active"] == 1
    assert mb["_password"] == GOOD_PW
    got = ctx.invites.get(inv.id)
    assert got.status == "redeemed" and got.redeemed_address == "alice@example.com"


def test_password_never_stored_locally(ctx):
    token, _ = new_invite(ctx)
    register(ctx, token)
    dump = "\n".join(ctx.db._conn.iterdump())
    assert GOOD_PW not in dump and token not in dump


def test_redeemed_invitation_cannot_be_reused(ctx):
    token, _ = new_invite(ctx)
    assert register(ctx, token).status_code == 200
    r = register(ctx, token, username="bob")
    assert r.status_code in (409, 410) and "already been used" in r.text
    assert "bob@example.com" not in ctx.fake.mailboxes


def test_expired_invitation_rejected(ctx):
    token, _ = new_invite(ctx, days=7)
    ctx.clock.advance(7 * 86400 + 1)
    page = ctx.client.get(f"/join/i/{token}")
    assert page.status_code == 410 and "expired" in page.text
    r = register(ctx, token)
    assert r.status_code == 409 and "expired" in r.text
    assert not ctx.fake.mailboxes


def test_invitation_valid_until_just_before_expiry(ctx):
    token, _ = new_invite(ctx, days=7)
    ctx.clock.advance(7 * 86400 - 60)
    assert register(ctx, token).status_code == 200


def test_revoked_invitation_rejected(ctx):
    token, inv = new_invite(ctx)
    assert ctx.invites.revoke(inv.id)
    r = register(ctx, token)
    assert r.status_code == 409 and "withdrawn" in r.text
    assert not ctx.fake.mailboxes


def test_unknown_invitation_rejected(ctx):
    r = register(ctx, "not-a-real-token")
    assert r.status_code == 404 and "Check the link" in r.text
    assert ctx.client.get("/join/i/nope").status_code == 404


def test_duplicate_username_rejected_and_invite_kept(ctx):
    ctx.fake.mailboxes["alice@example.com"] = {"username": "alice@example.com"}
    token, inv = new_invite(ctx)
    r = register(ctx, token, username="alice")
    assert r.status_code == 409 and "taken" in r.text
    assert ctx.invites.get(inv.id).status == "valid"
    assert register(ctx, token, username="alice2").status_code == 200


def test_alias_counts_as_taken(ctx):
    ctx.fake.aliases.add("info2@example.com")
    token, _ = new_invite(ctx)
    r = register(ctx, token, username="info2")
    assert r.status_code == 409 and "taken" in r.text


def test_race_where_mailcow_reports_exists(ctx):
    # Address free at check time but taken by the time of creation.
    token, inv = new_invite(ctx)
    orig = ctx.fake.handle

    def racing(req):
        if req.method == "POST":
            ctx.fake.mailboxes["alice@example.com"] = {"username": "alice@example.com"}
        return orig(req)
    import httpx
    from app.mailcow import MailcowClient
    mc = MailcowClient("https://mail.example.com", "test-api-key", transport=httpx.MockTransport(racing))
    r = _register_with_client(ctx, mc, token)
    assert r.status_code == 409 and "taken" in r.text
    assert ctx.invites.get(inv.id).status == "valid"


def _register_with_client(ctx, mc, token):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.security import RateLimiter
    app = create_app(ctx.settings, mailcow=mc, db=ctx.db, limiter=RateLimiter(clock=ctx.clock), clock=ctx.clock)
    ctx.client = TestClient(app, base_url="https://mail.example.com")
    return register(ctx, token)


def test_reserved_names_rejected(ctx):
    token, inv = new_invite(ctx)
    for name in ["admin", "administrator", "postmaster", "abuse", "hostmaster", "webmaster",
                 "support", "security", "mail", "root", "Post.Master", "no-reply"]:
        ctx.app.state.limiter.reset()
        r = register(ctx, token, username=name)
        assert r.status_code == 400 and "reserved" in r.text, name
    assert not ctx.fake.mailboxes and ctx.invites.get(inv.id).status == "valid"


def test_invalid_usernames_rejected(ctx):
    token, _ = new_invite(ctx)
    for name in ["ab", "-alice", "alice.", "al..ice", "al ice", "alice@example.com", "älice", "a" * 33, ""]:
        r = register(ctx, token, username=name)
        assert r.status_code == 400, name
    assert not ctx.fake.mailboxes


def test_weak_passwords_rejected(ctx):
    token, _ = new_invite(ctx)
    cases = [("short1A!", None), ("alllowercaseletters", None), ("password123", None),
             ("Alice-Is-Great-99", None), (GOOD_PW, GOOD_PW + "x")]
    for pw, confirm in cases:
        r = register(ctx, token, username="alice", password=pw, confirm=confirm)
        assert r.status_code == 400, pw
    assert not ctx.fake.mailboxes
    # A long passphrase is fine without symbols/capitals.
    assert register(ctx, token, password="correct horse battery staple").status_code == 200


def test_failed_provisioning_keeps_invitation(ctx):
    token, inv = new_invite(ctx)
    for mode in ["http500", "danger", "timeout"]:
        ctx.fake.fail_next_add = mode
        r = register(ctx, token)
        assert r.status_code == 503, mode
        assert "has not been used" in r.text
        assert ctx.invites.get(inv.id).status == "valid", mode
    fails = ctx.db.query("SELECT * FROM events WHERE kind='provision_failed'")
    assert len(fails) == 3
    assert register(ctx, token).status_code == 200


def test_mailcow_down_keeps_invitation(ctx):
    token, inv = new_invite(ctx)
    ctx.fake.down = True
    r = register(ctx, token)
    assert r.status_code == 503 and ctx.invites.get(inv.id).status == "valid"


def test_timeout_after_create_is_recognised(ctx):
    token, inv = new_invite(ctx)
    ctx.fake.fail_next_add = "timeout_after_create"
    r = register(ctx, token)
    assert r.status_code == 200 and "alice@example.com" in r.text
    assert ctx.invites.get(inv.id).status == "redeemed"


def test_bad_api_key_is_a_failure_not_a_consumed_invite(ctx, env):
    from app.mailcow import MailcowClient
    token, inv = new_invite(ctx)
    mc = MailcowClient("https://mail.example.com", "wrong", transport=ctx.fake.transport())
    r = _register_with_client(ctx, mc, token)
    assert r.status_code == 503 and ctx.invites.get(inv.id).status == "valid"
    assert "auth" in ctx.db.one("SELECT detail FROM events WHERE kind='provision_failed'")["detail"]


def test_claim_blocks_concurrent_use_and_times_out(ctx):
    token, inv = new_invite(ctx)
    assert ctx.invites.claim(inv.id)
    assert not ctx.invites.claim(inv.id)
    r = register(ctx, token)
    assert r.status_code == 409 and "right now" in r.text
    ctx.clock.advance(301)
    assert register(ctx, token).status_code == 200


def test_csrf_required(ctx):
    token, _ = new_invite(ctx)
    r = ctx.client.post("/join/register", data={"token": token, "username": "alice",
                                                 "password": GOOD_PW, "password_confirm": GOOD_PW, "csrf": "x"})
    assert r.status_code == 400 and not ctx.fake.mailboxes


def test_registration_rate_limited(ctx):
    token, _ = new_invite(ctx)
    codes = [register(ctx, token, username="ab").status_code for _ in range(11)]
    assert codes[:10] == [400] * 10 and codes[10] == 429


def test_username_check_endpoint(ctx):
    token, inv = new_invite(ctx)
    ctx.fake.mailboxes["bob@example.com"] = {"username": "bob@example.com"}
    q = lambda u, t=token: ctx.client.get("/join/check", params={"token": t, "u": u})  # noqa: E731
    assert q("alice").json()["ok"] is True
    assert q("bob").json()["ok"] is False
    assert q("admin").json()["message"] == "That username is reserved."
    assert q("alice", "bad").status_code == 403  # no anonymous enumeration
    ctx.invites.revoke(inv.id)
    assert q("alice").status_code == 403


def test_code_entry_redirects(ctx):
    token, _ = new_invite(ctx)
    r = ctx.client.post("/join/", data={"code": f"https://mail.example.com/join/i/{token}"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/join/i/{token}"
    r = ctx.client.post("/join/", data={"code": f"  {token} "}, follow_redirects=True)
    assert "Create your email address" in r.text


def test_security_headers_and_health(ctx):
    r = ctx.client.get("/join/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "no-referrer"
    assert ctx.client.get("/join/healthz").json() == {"ok": True}
    assert ctx.client.get("/docs").status_code == 404
