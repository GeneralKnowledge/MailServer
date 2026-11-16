"""Security tightening: headers, display names, IP allow-lists. (The admin
session and TOTP tests left with the admin pages in PR 15.)"""

from conftest import register


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
