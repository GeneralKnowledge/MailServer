import re

from conftest import ADMIN_PW, TOTP, admin_login, csrf_of, register
from app.security import totp_now, verify_totp


def test_admin_requires_login(ctx):
    r = ctx.client.get("/join/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/join/admin/login")
    r = ctx.client.post("/join/admin/invites", data={"csrf": "x"}, follow_redirects=False)
    assert r.status_code == 303 and not ctx.invites.list()


def test_admin_login_needs_password_and_totp(ctx):
    page = ctx.client.get("/join/admin/login")
    bad = [("boss", "wrong", totp_now(TOTP, at=ctx.clock())), ("boss", ADMIN_PW, "000000"), ("other", ADMIN_PW, totp_now(TOTP, at=ctx.clock()))]
    for u, p, code in bad:
        r = ctx.client.post("/join/admin/login", data={"username": u, "password": p, "code": code, "csrf": csrf_of(page.text)})
        assert r.status_code == 401
    assert admin_login(ctx).status_code == 303
    assert ctx.client.get("/join/admin").status_code == 200


def test_admin_login_rate_limited(ctx):
    page = ctx.client.get("/join/admin/login")
    for _ in range(5):
        ctx.client.post("/join/admin/login", data={"username": "boss", "password": "x", "code": "1", "csrf": csrf_of(page.text)})
    assert admin_login(ctx).status_code == 429


def test_admin_session_idle_timeout(ctx):
    admin_login(ctx)
    ctx.clock.advance(3601)
    assert ctx.client.get("/join/admin", follow_redirects=False).status_code == 303


def test_admin_create_invite_and_use_it(ctx):
    admin_login(ctx)
    page = ctx.client.get("/join/admin")
    r = ctx.client.post("/join/admin/invites", data={"csrf": csrf_of(page.text), "quota_mb": "5120", "days": "3", "note": "for Carol"})
    assert r.status_code == 200 and "Copy it now" in r.text
    link = re.search(r'id="newlink" readonly value="([^"]+)"', r.text).group(1)
    assert link.startswith("https://mail.example.com/join/i/")
    token = link.rsplit("/", 1)[1]
    # shown only once
    assert "Copy it now" not in ctx.client.get("/join/admin").text
    assert register(ctx, token, username="carol").status_code == 200
    assert ctx.fake.mailboxes["carol@example.com"]["quota"] == 5120 * 1048576
    page = ctx.client.get("/join/admin").text
    assert "carol@example.com" in page and "for Carol" in page and "redeemed" in page


def test_admin_quota_bounds(ctx):
    admin_login(ctx)
    page = ctx.client.get("/join/admin")
    r = ctx.client.post("/join/admin/invites", data={"csrf": csrf_of(page.text), "quota_mb": "999999", "days": "7"})
    assert "Quota must be" in r.text and not ctx.invites.list()


def test_admin_revoke_and_extend(ctx):
    admin_login(ctx)
    token, inv = ctx.invites.create(quota_mb=2048, days=1)
    ctx.clock.advance(2 * 86400)
    assert ctx.invites.get(inv.id).status == "expired"
    admin_login(ctx)
    csrf = csrf_of(ctx.client.get("/join/admin").text)
    ctx.client.post(f"/join/admin/invites/{inv.id}/extend", data={"csrf": csrf, "days": "7"})
    assert ctx.invites.get(inv.id).status == "valid"
    ctx.client.post(f"/join/admin/invites/{inv.id}/revoke", data={"csrf": csrf})
    assert ctx.invites.get(inv.id).status == "revoked"
    # can't un-revoke by extending
    ctx.client.post(f"/join/admin/invites/{inv.id}/extend", data={"csrf": csrf, "days": "7"})
    assert ctx.invites.get(inv.id).status == "revoked"


def test_admin_sees_provisioning_failures(ctx):
    admin_login(ctx)
    token, _ = ctx.invites.create(quota_mb=2048, days=7)
    ctx.fake.fail_next_add = "danger"
    register(ctx, token, username="dave")
    page = ctx.client.get("/join/admin").text
    assert "dave@example.com" in page and "mailbox_quota_left_exceeded" in page


def test_redeemed_invite_cannot_be_revoked(ctx):
    token, inv = ctx.invites.create(quota_mb=2048, days=7)
    register(ctx, token)
    assert not ctx.invites.revoke(inv.id)


def test_totp_known_vector():
    # RFC 6238 test secret "12345678901234567890" at T=59 -> 94287082 (8 digits) -> last 6: 287082
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp_now(secret, at=59) == "287082"
    assert verify_totp(secret, "287082", at=59) and not verify_totp(secret, "287083", at=59)
