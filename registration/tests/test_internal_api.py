"""PR 15: the sign-up app's internal invitation API, for the admin console only
(ADR 0020). Invitations stay in this app's own database. Fakes only."""

from conftest import CONSOLE_TOKEN, console, register
from fastapi.testclient import TestClient

from app.invites import hash_token

URL = "/internal/v1/invitations"


def test_create_shows_the_token_once_and_logs_who(ctx):
    r = console(ctx).post(URL, json={"quota_mb": 1024, "days": 3, "note": "for\nSam"})
    assert r.status_code == 201, r.text
    d = r.json()
    tok, inv = d["token"], d["invitation"]
    assert d["link"] == f"https://mail.example.com/join/i/{tok}"
    assert inv["status"] == "valid" and inv["quota_mb"] == 1024 and inv["note"] == "for Sam" and inv["hint"] == tok[:6]
    assert tok not in str(ctx.db.query("SELECT * FROM invitations")[0].keys()) and \
        hash_token(tok) == ctx.db.one("SELECT token_hash FROM invitations")["token_hash"]
    ev = ctx.db.one("SELECT * FROM events WHERE kind='invite_created'")
    assert "by=admin:admin-sub-1" in ev["detail"] and ev["ip"] == "172.30.240.14"
    listed = console(ctx).get(URL).json()
    assert [i["id"] for i in listed["invitations"]] == [inv["id"]] and tok not in str(listed)
    assert listed["defaults"]["max_quota_mb"] == 10240
    # ...and the invitation works for sign-up.
    assert register(ctx, tok).status_code == 200
    assert console(ctx).get(URL).json()["invitations"][0]["redeemed_address"] == "alice@example.com"


def test_defaults_and_bounds(ctx):
    r = console(ctx).post(URL, json={})
    assert r.status_code == 201 and r.json()["invitation"]["quota_mb"] == 2048
    for body in ({"quota_mb": 999999}, {"days": 91}, {"quota_mb": "lots"}):
        assert console(ctx).post(URL, json=body).status_code == 422


def test_revoke_and_extend(ctx):
    inv = console(ctx).post(URL, json={"days": 1}).json()["invitation"]
    r = console(ctx).post(f"{URL}/{inv['id']}/extend", json={"days": 30})
    assert r.status_code == 200 and r.json()["invitation"]["expires_at"] == int(ctx.clock()) + 30 * 86400
    assert console(ctx).post(f"{URL}/{inv['id']}/extend", json={"days": 0}).status_code == 422
    assert console(ctx).post(f"{URL}/{inv['id']}/revoke").json()["invitation"]["status"] == "revoked"
    r = console(ctx).post(f"{URL}/{inv['id']}/revoke")
    assert r.status_code == 409 and "revoked" in r.json()["detail"]
    assert console(ctx).post(f"{URL}/999/revoke").status_code == 404


def test_failures_are_listed(ctx):
    ctx.db.log("provision_failed", address="x@example.com", detail="limit: cap")
    f = console(ctx).get(URL).json()["failures"]
    assert f[0]["address"] == "x@example.com" and "limit" in f[0]["detail"]


def test_refused_without_the_token_from_elsewhere_or_through_nginx(ctx):
    good = {"Authorization": f"Bearer {CONSOLE_TOKEN}"}
    # The public (any other address), even with the right token.
    assert ctx.client.get(URL, headers=good).status_code == 403
    # Through mailcow's nginx (it always sets X-Forwarded-For).
    assert console(ctx).get(URL, headers={"X-Forwarded-For": "172.30.240.14"}).status_code == 403
    # Wrong token from the right address.
    assert console(ctx).get(URL, headers={"Authorization": "Bearer " + "x" * 40}).status_code == 401
    # The sign-up token is not the console's.
    assert console(ctx).get(URL, headers={"Authorization": "Bearer " + "b" * 40}).status_code == 401
    # The public path prefix never reaches it.
    assert ctx.client.get(f"/join{URL}").status_code == 404
    assert not ctx.db.query("SELECT * FROM invitations")


def test_off_without_a_console_token(env):
    from conftest import make_broker_client
    from fake_mailcow import FakeMailcow

    from app.config import load_settings
    from app.main import create_app
    app = create_app(load_settings({**env, "ADMIN_CONSOLE_TOKEN": ""}), broker=make_broker_client(FakeMailcow()))
    c = TestClient(app, client=("172.30.240.14", 1))
    assert c.get(URL, headers={"Authorization": "Bearer "}).status_code == 403
