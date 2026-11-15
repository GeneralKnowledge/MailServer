"""Security tightening: sessions, TOTP replay, admin IP allow-list, headers."""

from fastapi.testclient import TestClient

from conftest import ADMIN_PW, TOTP, admin_login, csrf_of, register
from app.config import load_settings
from app.db import Database
from app.main import create_app
from app.mailcow import MailcowClient
from app.security import RateLimiter, totp_now


def test_totp_code_cannot_be_reused(ctx):
    assert admin_login(ctx).status_code == 303
    other = TestClient(ctx.app, base_url="https://mail.example.com")
    page = other.get("/join/admin/login")
    r = other.post("/join/admin/login", data={"username": "boss", "password": ADMIN_PW,
                                               "code": totp_now(TOTP, at=ctx.clock()), "csrf": csrf_of(page.text)})
    assert r.status_code == 401
    assert "code reused" in ctx.db.one("SELECT detail FROM events WHERE kind='admin_login_failed'")["detail"]
    ctx.clock.advance(30)
    r = other.post("/join/admin/login", data={"username": "boss", "password": ADMIN_PW,
                                               "code": totp_now(TOTP, at=ctx.clock()), "csrf": csrf_of(page.text)},
                   follow_redirects=False)
    assert r.status_code == 303


def test_logout_kills_copied_cookie(ctx):
    admin_login(ctx)
    stolen = dict(ctx.client.cookies)
    csrf = csrf_of(ctx.client.get("/join/admin").text)
    ctx.client.post("/join/admin/logout", data={"csrf": csrf})
    thief = TestClient(ctx.app, base_url="https://mail.example.com", cookies=stolen)
    assert thief.get("/join/admin", follow_redirects=False).status_code == 303


def test_logout_requires_csrf(ctx):
    admin_login(ctx)
    ctx.client.post("/join/admin/logout", data={"csrf": "nope"})
    assert ctx.client.get("/join/admin", follow_redirects=False).status_code == 200


def test_sign_out_everywhere(ctx):
    admin_login(ctx)
    other = TestClient(ctx.app, base_url="https://mail.example.com")
    ctx.clock.advance(30)
    page = other.get("/join/admin/login")
    other.post("/join/admin/login", data={"username": "boss", "password": ADMIN_PW,
                                          "code": totp_now(TOTP, at=ctx.clock()), "csrf": csrf_of(page.text)})
    assert other.get("/join/admin", follow_redirects=False).status_code == 200
    csrf = csrf_of(ctx.client.get("/join/admin").text)
    ctx.client.post("/join/admin/logout-all", data={"csrf": csrf})
    assert other.get("/join/admin", follow_redirects=False).status_code == 303
    assert ctx.client.get("/join/admin", follow_redirects=False).status_code == 303


def test_global_login_limit(ctx):
    # Many IPs each staying under the per-IP limit still hit the global cap.
    page = ctx.client.get("/join/admin/login")
    codes = []
    for i in range(31):
        ctx.app.state.limiter._hits.pop(("login", "testclient"), None)  # pretend a new IP each time
        r = ctx.client.post("/join/admin/login", data={"username": "boss", "password": "x", "code": "123456",
                                                        "csrf": csrf_of(page.text)})
        codes.append(r.status_code)
    assert codes[:30] == [401] * 30 and codes[30] == 429


def _app_with(env, **extra):
    from fake_mailcow import FakeMailcow
    settings = load_settings({**env, **extra})
    fake = FakeMailcow()
    mc = MailcowClient(settings.mailcow_api_url, settings.mailcow_api_key, transport=fake.transport())
    return create_app(settings, mailcow=mc, db=Database(":memory:"), limiter=RateLimiter())


def test_admin_ip_allow_list(env):
    blocked = TestClient(_app_with(env, ADMIN_ALLOWED_IPS="10.8.0.0/24"), base_url="https://mail.example.com")
    assert blocked.get("/join/admin/login").status_code == 404
    assert blocked.get("/join/admin").status_code == 404
    assert blocked.get("/join/").status_code == 200  # public pages unaffected
    # TestClient's client host is "testclient", not an IP: refused whenever a list is set
    listed = TestClient(_app_with(env, ADMIN_ALLOWED_IPS="127.0.0.1"), base_url="https://mail.example.com")
    assert listed.get("/join/admin/login").status_code == 404
    open_app = TestClient(_app_with(env), base_url="https://mail.example.com")
    assert open_app.get("/join/admin/login").status_code == 200


def test_extra_headers(ctx):
    h = ctx.client.get("/join/").headers
    assert h["strict-transport-security"].startswith("max-age=")
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert "camera=()" in h["permissions-policy"]


def test_display_name_control_chars_stripped(ctx):
    token, _ = ctx.invites.create(quota_mb=2048, days=7)
    assert register(ctx, token, name="Ali\x00ce‮ Evil\r\nBcc: x").status_code == 200
    assert ctx.fake.mailboxes["alice@example.com"]["name"] == "Alice Evil Bcc: x"


def test_ip_allowed_unit():
    from app.security import ip_allowed, parse_networks
    nets = parse_networks("10.8.0.0/24, 2001:db8::/32 ,203.0.113.7")
    assert ip_allowed("10.8.0.9", nets) and ip_allowed("203.0.113.7", nets) and ip_allowed("2001:db8::1", nets)
    assert not ip_allowed("10.8.1.1", nets) and not ip_allowed("junk", nets)
    assert ip_allowed("1.2.3.4", parse_networks(""))
