"""PR 14: the portal shell. Fakes only (Keycloak token endpoint, control plane)."""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import ISSUER, PORTAL_TOKEN, sign_in

from app.config import load_settings


def test_signed_out_goes_to_sign_in(ctx):
    r = ctx.client.get("/account/")
    assert r.status_code == 303 and r.headers["location"] == "/account/login"


def test_login_uses_code_flow_with_pkce_and_state(ctx):
    r = ctx.client.get("/account/login")
    url = urlparse(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/protocol/openid-connect/auth"
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == "https://mx.example.com/account/callback"
    assert q["client_id"] == "avedeus-portal" and "openid" in q["scope"]
    assert len(q["state"]) >= 24 and len(q["nonce"]) >= 24


def test_full_sign_in_shows_account_and_native_links(ctx):
    r, q = sign_in(ctx)
    assert r.status_code == 303 and r.headers["location"] == "/account/"
    # PKCE: the verifier sent to the token endpoint matches the challenge sent to the browser.
    verifier = ctx.kc.forms[-1]["code_verifier"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == q["code_challenge"]
    page = ctx.client.get("/account/")
    assert page.status_code == 200
    t = page.text
    assert "alice@example.com" in t and "512 MB of 2048 MB used (25%)" in t
    assert "app password" in t
    assert f"{ISSUER}/account/account-security/signing-in" in t and "https://mx.example.com/user" in t
    assert "https://mx.example.com/SOGo/" in t
    # The control plane got the portal's token and the user's access token, nothing naming the user.
    call = ctx.cp.calls[-1]
    assert call["authorization"] == f"Bearer {PORTAL_TOKEN}" and call["x-user-token"].startswith("access-")


def test_no_tokens_in_the_cookie(ctx):
    sign_in(ctx)
    cookie = ctx.client.cookies.get("portal_session")
    raw = base64.b64decode(cookie.split(".")[0] + "==").decode(errors="ignore")
    assert "access-" not in raw and "refresh-1" not in raw and "sid" in raw


def test_wrong_state_is_refused(ctx):
    ctx.client.get("/account/login")
    r = ctx.client.get("/account/callback", params={"code": "x", "state": "forged"})
    assert r.status_code == 400 and not ctx.kc.forms


def test_nonce_mismatch_is_refused(ctx):
    ctx.kc.nonce_override = "replayed"
    r, _ = sign_in(ctx)
    assert r.status_code == 502
    assert ctx.client.get("/account/").status_code == 303


def test_stale_login_is_refused(ctx):
    r = ctx.client.get("/account/login")
    q = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    ctx.clock.t += 900
    r = ctx.client.get("/account/callback", params={"code": ctx.kc.issue_code(q["nonce"], ""), "state": q["state"]})
    assert r.status_code == 400


def test_expired_access_token_is_refreshed(ctx):
    sign_in(ctx)
    ctx.clock.t += 400
    assert ctx.client.get("/account/").status_code == 200
    assert ctx.kc.forms[-1]["grant_type"] == "refresh_token"


def test_control_plane_refusing_the_token_signs_out(ctx):
    sign_in(ctx)
    ctx.cp.status = 401
    r = ctx.client.get("/account/")
    assert r.status_code == 303 and r.headers["location"] == "/account/login"


def test_control_plane_down_still_shows_links(ctx):
    sign_in(ctx)
    ctx.cp.status = 502
    page = ctx.client.get("/account/")
    assert page.status_code == 200 and "can't be shown right now" in page.text and "app password" in page.text


def test_logout_ends_session_at_keycloak(ctx):
    sign_in(ctx)
    r = ctx.client.get("/account/logout")
    url = urlparse(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert url.path.endswith("/protocol/openid-connect/logout") and "id_token_hint" in q
    assert q["post_logout_redirect_uri"] == "https://mx.example.com/account/"
    assert ctx.client.get("/account/").status_code == 303


def test_security_headers(ctx):
    r = ctx.client.get("/account/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_settings_insist_on_https_and_long_secrets(env):
    with pytest.raises(RuntimeError, match="https"):
        load_settings({**env, "PUBLIC_URL": "http://mx.example.com"})
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        load_settings({**env, "SESSION_SECRET": "short"})
