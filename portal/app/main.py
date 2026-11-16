"""Avedeus portal (PR 14): one place for a person's account at /account.

It signs people in with Keycloak (OpenID Connect, authorization code + PKCE,
confidential client `avedeus-portal`), shows their address and quota from
the control plane, and otherwise **links** to what mailcow, SOGo and
Keycloak already do (ADR 0016): password, two-factor and sessions in
Keycloak's account console; app passwords, login history, spam settings,
quarantine and device setup in mailcow's user page; mail, filters and
vacation in SOGo. It rebuilds none of them.

It holds no upstream credential (ADR 0003): only its own control-plane
token and its Keycloak client secret. The user's identity reaches the control
plane only as their Keycloak access token, which the control plane verifies.
Sessions live in memory: a restart signs everyone out, and there is nothing
to back up.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings, load_settings
from .oidc import OidcClient, OidcError

log = logging.getLogger("portal")
HERE = Path(__file__).resolve().parent
SESSION_SECONDS = 8 * 3600


@dataclass
class UserSession:
    sub: str
    name: str
    email: str
    access_token: str
    access_until: float
    refresh_token: str
    id_token: str
    started: float


class Sessions:
    """Server-side sessions; the cookie holds only a random id."""

    def __init__(self, clock=time.time):
        self.clock, self.lock, self.items = clock, threading.Lock(), {}

    def put(self, s: UserSession) -> str:
        sid = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.items = {k: v for k, v in self.items.items() if now - v.started < SESSION_SECONDS}
            self.items[sid] = s
        return sid

    def get(self, sid: str | None) -> UserSession | None:
        if not sid:
            return None
        with self.lock:
            s = self.items.get(sid)
            if s and self.clock() - s.started >= SESSION_SECONDS:
                self.items.pop(sid, None)
                return None
            return s

    def drop(self, sid: str | None) -> UserSession | None:
        with self.lock:
            return self.items.pop(sid, None) if sid else None


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def create_app(settings: Settings | None = None, *, oidc: OidcClient | None = None,
               control_plane: httpx.BaseTransport | None = None, clock=time.time) -> FastAPI:
    s = settings or load_settings()
    op = oidc or OidcClient(s.oidc_issuer, s.keycloak_internal_url, s.client_id, s.client_secret)
    sessions = Sessions(clock)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    links = s.links()
    templates.env.globals.update(brand=s.brand_name, base=s.base_path, links=links)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sessions = sessions
    # The signed cookie carries only the login handshake (state, nonce, PKCE
    # verifier) and the session id; tokens stay on the server.
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret, session_cookie="portal_session",
                       max_age=SESSION_SECONDS, path=s.base_path or "/", same_site="lax",
                       https_only=s.secure_cookies)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; "
                                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()")
        if s.secure_cookies:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    r = APIRouter(prefix=s.base_path)

    def page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def problem(request: Request, status: int, title: str, message: str) -> HTMLResponse:
        return page(request, "message.html", status, title=title, message=message)

    def current(request: Request) -> UserSession | None:
        u = sessions.get(request.session.get("sid"))
        if u and clock() >= u.access_until - 15:
            try:
                t = op.refresh(u.refresh_token)
            except OidcError:
                sessions.drop(request.session.pop("sid", None))
                return None
            u.access_token, u.access_until = t["access_token"], clock() + int(t.get("expires_in", 60))
            u.refresh_token = t.get("refresh_token", u.refresh_token)
        return u

    def own_account(u: UserSession) -> tuple[dict | None, str]:
        try:
            with httpx.Client(base_url=s.control_plane_url, transport=control_plane, timeout=httpx.Timeout(15.0)) as c:
                resp = c.get("/v1/me", headers={"Authorization": f"Bearer {s.control_plane_token}",
                                                "X-User-Token": u.access_token})
        except httpx.HTTPError as e:
            log.warning("control plane not answering: %s", type(e).__name__)
            return None, "unavailable"
        if resp.status_code == 200:
            return resp.json(), ""
        try:
            kind = resp.json().get("kind", "")
        except ValueError:
            kind = ""
        log.warning("control plane refused /v1/me: HTTP %s %s", resp.status_code, kind)
        return None, kind or "unavailable"

    @app.get(f"{s.base_path}/healthz")
    def healthz():
        return {"ok": True}

    app.mount(f"{s.base_path}/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @r.get("/")
    def home(request: Request):
        u = current(request)
        if not u:
            return RedirectResponse(f"{s.base_path}/login", status_code=303)
        account, err = own_account(u)
        if err == "unauthorized":
            sessions.drop(request.session.pop("sid", None))
            return RedirectResponse(f"{s.base_path}/login", status_code=303)
        pct = None
        if account and account.get("quota_mb"):
            pct = min(100, round(100 * account["used_mb"] / account["quota_mb"]))
        return page(request, "account.html", user=u, account=account, error=err, quota_pct=pct)

    @r.get("/login")
    def login(request: Request):
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = _pkce()
        request.session.update({"state": state, "nonce": nonce, "pkce": verifier, "t": clock()})
        q = urlencode({"client_id": s.client_id, "response_type": "code", "scope": "openid profile email",
                       "redirect_uri": s.redirect_uri, "state": state, "nonce": nonce,
                       "code_challenge": challenge, "code_challenge_method": "S256"})
        return RedirectResponse(f"{s.oidc_issuer}/protocol/openid-connect/auth?{q}", status_code=303)

    @r.get("/callback")
    def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        expected, nonce, verifier = (request.session.pop(k, None) for k in ("state", "nonce", "pkce"))
        started = request.session.pop("t", 0) or 0
        if error:
            return problem(request, 400, "Sign-in was cancelled", "Nothing was changed. You can try again.")
        if not expected or not secrets.compare_digest(state, expected) or clock() - float(started) > 600:
            return problem(request, 400, "Sign-in expired", "That sign-in link is no longer valid. Please start again.")
        try:
            t = op.exchange(code, verifier, s.redirect_uri)
            claims = op.id_claims(t["id_token"], nonce)
        except OidcError as e:
            log.warning("sign-in failed: %s", e)
            return problem(request, 502, "Couldn't sign you in", "The sign-in service didn't answer as expected. "
                                                                  "Please try again in a moment.")
        u = UserSession(sub=claims["sub"], name=str(claims.get("name") or claims.get("preferred_username", ""))[:100],
                        email=str(claims.get("email", ""))[:254], access_token=t["access_token"],
                        access_until=clock() + int(t.get("expires_in", 60)),
                        refresh_token=t.get("refresh_token", ""), id_token=t["id_token"], started=clock())
        old = request.session.get("sid")
        sessions.drop(old)
        request.session["sid"] = sessions.put(u)
        return RedirectResponse(f"{s.base_path}/", status_code=303)

    @r.get("/logout")
    def logout(request: Request):
        u = sessions.drop(request.session.pop("sid", None))
        request.session.clear()
        q = {"client_id": s.client_id, "post_logout_redirect_uri": f"{s.public_url}{s.base_path}/"}
        if u:
            q["id_token_hint"] = u.id_token
        return RedirectResponse(f"{s.oidc_issuer}/protocol/openid-connect/logout?{urlencode(q)}", status_code=303)

    app.include_router(r)
    return app


def get_app() -> FastAPI:  # uvicorn --factory app.main:get_app
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
