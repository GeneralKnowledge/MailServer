"""PR 15 and PR 16: the admin console. Fakes only (Keycloak token endpoint, control
plane, the sign-up app's internal API). Not proven on a server."""

import json
import re
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import CP_TOKEN, SIGNUP_TOKEN, csrf_of, sign_in
from fastapi.testclient import TestClient

from app.config import load_settings


def test_signed_out_goes_to_sign_in(ctx):
    r = ctx.client.get("/console/")
    assert r.status_code == 303 and r.headers["location"] == "/console/login"


def test_sign_in_always_asks_again(ctx):
    q = parse_qs(urlparse(ctx.client.get("/console/login").headers["location"]).query)
    assert q["client_id"] == ["avedeus-admin"] and q["prompt"] == ["login"] and q["max_age"] == ["0"]
    assert q["code_challenge_method"] == ["S256"]


def test_admin_signs_in_and_sees_invitations(ctx):
    r, _ = sign_in(ctx)
    assert r.status_code == 303 and r.headers["location"] == "/console/"
    page = ctx.client.get("/console/")
    assert page.status_code == 200 and "abcdef" in page.text and "Sam" in page.text
    method, path, headers, _, _ = ctx.signup.calls[-1]
    assert headers["authorization"] == f"Bearer {SIGNUP_TOKEN}" and headers["x-admin-actor"] == "admin-1"


@pytest.mark.parametrize("claims, why", [
    ({"groups": ["/users"], "amr": ["pwd", "otp"]}, "not in the admins group"),
    ({"groups": ["/admins"], "amr": ["pwd"]}, "one-time code"),
    ({"groups": ["/admins"]}, "one-time code"),
])
def test_only_admins_with_a_second_factor(ctx, claims, why):
    ctx.kc.claims = claims
    r, _ = sign_in(ctx)
    assert r.status_code == 403 and why in r.text
    assert ctx.client.get("/console/").status_code == 303


def test_hidden_from_other_addresses(ctx):
    other = TestClient(ctx.app, base_url="https://mx.example.com", client=("198.51.100.9", 1))
    assert other.get("/console/login").status_code == 404
    assert other.get("/console/healthz").status_code == 200  # the container health check


def test_create_shows_the_link_once_and_needs_csrf(ctx):
    sign_in(ctx)
    ctx.signup.answers[("POST", "/internal/v1/invitations")] = (201, {
        "ok": True, "token": "SECRET-TOKEN-123", "link": "https://mx.example.com/join/i/SECRET-TOKEN-123",
        "invitation": {"id": 2, "expires_at": 1_800_600_000}})
    r = ctx.client.post("/console/invitations", data={"quota_mb": "1024", "days": "3", "note": "Bo", "csrf": "nope"})
    assert r.status_code == 400 and not any(c[0] == "POST" for c in ctx.signup.calls)
    r = ctx.client.post("/console/invitations", data={"quota_mb": "1024", "days": "3", "note": "Bo",
                                                    "csrf": csrf_of(ctx)})
    assert r.status_code == 200 and "SECRET-TOKEN-123" in r.text and "won't be shown again" in r.text
    assert ctx.signup.calls[-2][3] == {"quota_mb": 1024, "days": 3, "note": "Bo"}
    assert "SECRET-TOKEN-123" not in ctx.client.get("/console/").text
    assert "SECRET" not in str(dict(ctx.client.cookies))


def test_revoke_explains_a_refusal(ctx):
    sign_in(ctx)
    ctx.signup.answers[("POST", "/internal/v1/invitations/1/revoke")] = (409, {"ok": False, "kind": "conflict",
                                                                              "detail": "invitation is in_progress"})
    r = ctx.client.post("/console/invitations/1/revoke", data={"csrf": csrf_of(ctx)})
    assert r.status_code == 303
    assert "be revoked: invitation is in_progress" in ctx.client.get("/console/").text
    assert ctx.client.post("/console/invitations/1/delete", data={"csrf": csrf_of(ctx)}).status_code == 303
    assert not any(c[1].endswith("/delete") for c in ctx.signup.calls)


def test_accounts_trace_and_audit_carry_the_admins_token(ctx):
    sign_in(ctx)
    ctx.cp.answers[("GET", "/v1/accounts")] = (200, {"ok": True, "identity_provider": "keycloak", "accounts": [
        {"address": "alice@example.com", "name": "Alice", "quota_mb": 2048, "used_mb": 3, "active": True,
         "created": "", "identity_linked": True}]})
    ctx.cp.answers[("GET", "/v1/audit")] = (200, {"ok": True, "events": [
        {"time": "2026-11-15T10:00:00Z", "action": "account.create", "outcome": "ok", "caller": "signup",
         "actor": None, "subject": {"address": "alice@example.com"}, "error_kind": None}]})
    ctx.cp.answers[("GET", "/v1/trace")] = (200, {"found": False, "messages": [], "rejections": [], "notes": [],
                                                 "limits": ["only retained logs"],
                                                 "window": {"postfix_lines": 10, "oldest_log_entry": 1_800_000_000}})
    assert "alice@example.com" in ctx.client.get("/console/accounts").text
    assert "account.create" in ctx.client.get("/console/audit", params={"action": "account"}).text
    assert ctx.cp.calls[-1][4]["action"] == "account"
    assert ctx.client.get("/console/trace").status_code == 200 and not ctx.cp.calls[2:]
    t = ctx.client.get("/console/trace", params={"address": "alice@example.com"}).text
    assert "Nothing matched" in t and "only retained logs" in t
    for _, _, headers, _, _ in ctx.cp.calls:
        assert headers["authorization"] == f"Bearer {CP_TOKEN}" and headers["x-user-token"].startswith("access-")


def test_control_plane_refusing_the_token_signs_out(ctx):
    sign_in(ctx)
    ctx.cp.answers[("GET", "/v1/accounts")] = (401, {"ok": False, "kind": "unauthorized", "detail": "x"})
    assert ctx.client.get("/console/accounts").status_code == 401
    assert ctx.client.get("/console/").status_code == 303


def test_idle_sessions_end(ctx):
    sign_in(ctx)
    ctx.clock.t += 3601
    assert ctx.client.get("/console/").status_code == 303


def test_logout_needs_csrf_and_ends_the_keycloak_session(ctx):
    sign_in(ctx)
    assert ctx.client.post("/console/logout", data={"csrf": "x"}).headers["location"] == "/console/"
    r = ctx.client.post("/console/logout", data={"csrf": csrf_of(ctx)})
    assert "/protocol/openid-connect/logout" in r.headers["location"] and "id_token_hint" in r.headers["location"]
    assert ctx.client.get("/console/").status_code == 303


def test_security_headers(ctx):
    h = ctx.client.get("/console/login").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["cache-control"] == "no-store"
    assert h["x-robots-tag"] == "noindex, nofollow"


def test_settings(env):
    s = load_settings(env)
    assert s.client_id == "avedeus-admin" and s.require_otp and s.signup_url == "http://registration:8000"
    with pytest.raises(RuntimeError, match="ADMIN_ALLOWED_IPS"):
        load_settings({**env, "ADMIN_ALLOWED_IPS": ""})
    assert load_settings({**env, "ADMIN_ALLOWED_IPS": "", "ADMIN_ALLOW_ANY": "1"}).allowed_ips == ()
    with pytest.raises(RuntimeError, match="must differ"):
        load_settings({**env, "SIGNUP_CONSOLE_TOKEN": env["CONTROL_PLANE_TOKEN"]})
    with pytest.raises(RuntimeError, match="https"):
        load_settings({**env, "PUBLIC_URL": "http://mx.example.com"})
    with pytest.raises(RuntimeError, match="not a valid"):
        load_settings({**env, "ADMIN_ALLOWED_IPS": "home"})


# ---- PR 16: one account and its lifecycle ------------------------------------

ACCOUNT = {"ok": True, "address": "alice@example.com", "name": "Alice", "quota_mb": 2048, "used_mb": 3,
           "active": True, "state": "active", "identity": {"linked": True, "enabled": True,
                                                           "password_reset_required": False},
           "deletion": None, "history": [
               {"time": "2026-11-15T10:00:00Z", "action": "account.quota", "outcome": "ok", "caller": "admin",
                "actor": {"kind": "admin", "id": "admin-1"}, "changes": {"quota_mb": {"from": 1024, "to": 2048}},
                "error_kind": None}]}


def account_form(c):
    page = c.client.get("/console/accounts/alice@example.com").text
    return {"csrf": re.search(r'name="csrf" value="([^"]+)"', page).group(1),
            "idem": re.search(r'name="idem" value="([^"]+)"', page).group(1)}, page


def test_account_page_shows_state_history_and_actions(ctx):
    sign_in(ctx)
    ctx.cp.answers[("GET", "/v1/accounts/alice@example.com")] = (200, ACCOUNT)
    form, page = account_form(ctx)
    assert "2048 MB" in page and "account.quota" in page and "1024 → 2048" in page
    assert "Suspend" in page and "Schedule deletion" in page and "Ask for a new password" in page
    assert ctx.cp.calls[-1][2]["x-user-token"].startswith("access-")


def test_suspend_from_the_console_carries_the_key_and_the_admin(ctx):
    sign_in(ctx)
    ctx.cp.answers[("GET", "/v1/accounts/alice@example.com")] = (200, ACCOUNT)
    ctx.cp.answers[("POST", "/v1/accounts/alice@example.com/suspend")] = (200, {
        "ok": True, "address": "alice@example.com", "state": "login_disabled", "identity": "disabled"})
    form, _ = account_form(ctx)
    r = ctx.client.post("/console/accounts/alice@example.com/suspend", data=form)
    assert r.status_code == 303 and r.headers["location"] == "/console/accounts/alice@example.com"
    method, path, headers, body, _ = ctx.cp.calls[-1]
    assert (method, body) == ("POST", {"stop_receiving": False})
    assert headers["idempotency-key"] == form["idem"] and headers["x-user-token"].startswith("access-")
    assert "mail still arrives" in ctx.client.get("/console/accounts/alice@example.com").text


def test_lifecycle_forms_need_csrf_and_deletion_needs_the_address_typed(ctx):
    sign_in(ctx)
    ctx.cp.answers[("GET", "/v1/accounts/alice@example.com")] = (200, ACCOUNT)
    form, _ = account_form(ctx)
    for action, data, says in (("suspend", {**form, "csrf": "wrong"}, "form expired"),
                               ("delete", {**form, "confirm": "bob@example.com"}, "type the address exactly"),
                               ("explode", form, "form expired")):
        n = len(ctx.cp.calls)
        ctx.client.post(f"/console/accounts/alice@example.com/{action}", data=data)
        assert len(ctx.cp.calls) == n, action
        assert says in ctx.client.get("/console/accounts/alice@example.com").text


def test_scheduling_a_deletion_and_a_refusal_are_explained(ctx):
    sign_in(ctx)
    ctx.cp.answers[("GET", "/v1/accounts/alice@example.com")] = (200, ACCOUNT)
    ctx.cp.answers[("POST", "/v1/accounts/alice@example.com/deletion")] = (202, {
        "ok": True, "address": "alice@example.com", "state": "login_disabled", "identity": "disabled",
        "due_at": "2026-12-15T10:00:00Z", "needs_backup_after": "2026-11-15T10:00:00Z"})
    form, _ = account_form(ctx)
    ctx.client.post("/console/accounts/alice@example.com/delete", data={**form, "confirm": "Alice@Example.com"})
    assert ctx.cp.calls[-1][:2] == ("POST", "/v1/accounts/alice@example.com/deletion")
    assert "Deletion scheduled for 2026-12-15 10:00:00 UTC" in ctx.client.get("/console/accounts/alice@example.com").text
    ctx.cp.answers[("PUT", "/v1/accounts/alice@example.com/quota")] = (422, {
        "ok": False, "kind": "invalid", "detail": "quota is below what the mailbox already uses (3 MB)"})
    ctx.client.post("/console/accounts/alice@example.com/quota", data={**form, "quota_mb": "1"})
    assert ctx.cp.calls[-1][3] == {"quota_mb": 1}
    assert "Not done: quota is below" in ctx.client.get("/console/accounts/alice@example.com").text


# ---- PR 17: several invitations at once, and a new link ------------------------

def test_bulk_invitations_show_every_link_once(ctx):
    sign_in(ctx)
    ctx.signup.answers[("POST", "/internal/v1/invitations/bulk")] = (201, {"ok": True, "created": [
        {"token": f"tok{n}", "link": f"https://mx.example.com/join/i/tok{n}",
         "invitation": {"id": n, "note": name, "expires_at": 1_800_600_000}} for n, name in ((2, "Sam"), (3, "Alex"))]})
    csrf = csrf_of(ctx)
    r = ctx.client.post("/console/invitations/bulk", data={"csrf": csrf, "names": " Sam \n\n Alex\n", "days": "5"})
    assert r.status_code == 200 and "2 invitations created" in r.text
    assert "/join/i/tok2" in r.text and "/join/i/tok3" in r.text
    assert ctx.signup.calls[-2][3] == {"notes": ["Sam", "Alex"], "quota_mb": None, "days": 5}
    assert "tok2" not in ctx.client.get("/console/").text
    n = len(ctx.signup.calls)
    assert ctx.client.post("/console/invitations/bulk", data={"csrf": csrf, "names": "  \n"}).status_code == 400
    assert ctx.client.post("/console/invitations/bulk", data={"csrf": "x", "names": "Sam"}).status_code == 400
    assert all(c[1] != "/internal/v1/invitations/bulk" for c in ctx.signup.calls[n:])


def test_new_link_for_an_invitation(ctx):
    sign_in(ctx)
    ctx.signup.answers[("POST", "/internal/v1/invitations/1/reissue")] = (200, {
        "ok": True, "token": "newtok", "link": "https://mx.example.com/join/i/newtok",
        "invitation": {"id": 1, "expires_at": 1_800_600_000}})
    csrf = csrf_of(ctx)
    r = ctx.client.post("/console/invitations/1/reissue", data={"csrf": csrf, "days": "7"})
    assert r.status_code == 200 and "old one no longer works" in r.text and "/join/i/newtok" in r.text
    assert ctx.signup.calls[-2][3] == {"days": 7}
    ctx.signup.answers[("POST", "/internal/v1/invitations/1/reissue")] = (409, {"ok": False, "detail": "invitation is redeemed"})
    r = ctx.client.post("/console/invitations/1/reissue", data={"csrf": csrf})
    assert r.status_code == 400 and "invitation is redeemed" in r.text


def _audit_pages(request):
    ev = lambda i, **kw: {"time": f"2026-11-15T10:0{i}:00Z", "action": "account.suspend", "outcome": "ok",  # noqa: E731
                          "caller": "admin", "actor": {"kind": "admin", "id": "sub-1"},
                          "subject": {"address": "alice@example.com"}, "error_kind": None, "id": f"e{i}", **kw}
    if request.url.params.get("before") == "7":
        return {"ok": True, "events": [ev(1, action="=cmd|' /C calc'!A0")], "next": None}
    return {"ok": True, "events": [ev(3), ev(2)], "next": 7}


def test_audit_filters_paging_and_export(ctx):
    # PR 28: account and date filters reach the control plane; export walks every page.
    sign_in(ctx)
    ctx.cp.answers[("GET", "/v1/audit")] = (200, _audit_pages)
    t = ctx.client.get("/console/audit", params={"address": "alice@example.com", "since": "2026-11-01",
                                                 "until": "2026-11-15", "outcome": "ok"}).text
    sent = ctx.cp.calls[-1][4]
    assert sent["address"] == "alice@example.com" and sent["since"] == "2026-11-01" and sent["until"] == "2026-11-15"
    assert "before=7" in t and "Older events" in t and "Keycloak events" in t and "/admin/dashboard" in t
    assert "format=csv&amp;" in t and "address=alice%40example.com" in t

    r = ctx.client.get("/console/audit/export", params={"format": "csv", "address": "alice@example.com"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="audit-' in r.headers["content-disposition"]
    rows = r.text.strip().splitlines()
    assert rows[0].startswith("time,action,outcome") and len(rows) == 4
    assert "admin:sub-1" in rows[1] and "'=cmd" in rows[3]          # no spreadsheet formulas
    pages = [c[4] for c in ctx.cp.calls if c[1] == "/v1/audit"][-2:]
    assert pages[0]["limit"] == "500" and "before" not in pages[0] and pages[1]["before"] == "7"
    assert all(p["address"] == "alice@example.com" for p in pages)

    r = ctx.client.get("/console/audit/export", params={"format": "jsonl"})
    lines = [json.loads(x) for x in r.text.splitlines()]
    assert [e["id"] for e in lines] == ["e3", "e2", "e1"]
    assert ctx.client.get("/console/audit/export", params={"format": "xml"}).status_code == 422


def test_audit_export_needs_a_signed_in_admin(ctx):
    r = ctx.client.get("/console/audit/export", follow_redirects=False)
    assert r.status_code in (302, 303) and not ctx.cp.calls
