"""Avedeus admin console (PR 15, ADR 0010, ADR 0020): its own container at /console
(mailcow's own admin login already lives at /admin).

Sign-in is Keycloak (OpenID Connect, code + PKCE, confidential client
`avedeus-admin`, whose browser flow always asks for password and one-time
code). Only members of the `admins` group who signed in with a second factor
get in, and only from ADMIN_ALLOWED_IPS. The console holds no upstream
credential (ADR 0003): its Keycloak client secret, its control-plane token
and its token for the sign-up app's internal API.

    Invitations  the sign-up app's internal API (they stay in its database, ADR 0020)
    Accounts     control plane listAccounts, getAccount and, PR 16, the lifecycle
                 operations (suspend, reactivate, quota, password reset, deletion)
    Trace        control plane traceMessage (PR 34)
    Audit        control plane readAudit

Every control-plane call carries the admin's own access token, which the
control plane verifies again (signature, client, group, second factor). Calls
to the sign-up app carry the admin's Keycloak id for its event log. Mailbox
and user management itself stays in mailcow's and Keycloak's admin consoles,
which the console links to. Sessions live in memory.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings, load_settings
from .oidc import OidcClient, OidcError

log = logging.getLogger("admin")
HERE = Path(__file__).resolve().parent
SESSION_SECONDS = 8 * 3600
IDLE_SECONDS = 3600


@dataclass
class AdminSession:
    sub: str
    name: str
    access_token: str
    access_until: float
    refresh_token: str
    id_token: str
    started: float
    seen: float
    csrf: str


class Sessions:
    """Server-side sessions; the cookie holds only a random id."""

    def __init__(self, clock=time.time):
        self.clock, self.lock, self.items = clock, threading.Lock(), {}

    def put(self, s: AdminSession) -> str:
        sid = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.items = {k: v for k, v in self.items.items()
                          if now - v.started < SESSION_SECONDS and now - v.seen < IDLE_SECONDS}
            self.items[sid] = s
        return sid

    def get(self, sid: str | None) -> AdminSession | None:
        if not sid:
            return None
        with self.lock:
            s = self.items.get(sid)
            now = self.clock()
            if s and (now - s.started >= SESSION_SECONDS or now - s.seen >= IDLE_SECONDS):
                self.items.pop(sid, None)
                return None
            if s:
                s.seen = now
            return s

    def drop(self, sid: str | None) -> AdminSession | None:
        with self.lock:
            return self.items.pop(sid, None) if sid else None


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _ts(ts) -> str:
    if not ts:
        return ""
    if isinstance(ts, str):
        return ts[:19].replace("T", " ") + " UTC" if "T" in ts else ts
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def admin_problem(claims: dict, group: str, require_otp: bool) -> str | None:
    """Why this signed-in person may not use the console, or None. The control
    plane checks the access token again on every call; this is the door."""
    groups = {str(g).lstrip("/") for g in claims.get("groups") or []}
    if group not in groups:
        return f"Your account is not in the {group} group."
    if require_otp and "otp" not in (claims.get("amr") or []):
        return ("That sign-in didn't use a one-time code. This happens right after you set one up: "
                "sign in again and enter a code.")
    return None


def create_app(settings: Settings | None = None, *, oidc: OidcClient | None = None,
               control_plane: httpx.BaseTransport | None = None, signup: httpx.BaseTransport | None = None,
               clock=time.time) -> FastAPI:
    s = settings or load_settings()
    op = oidc or OidcClient(s.oidc_issuer, s.keycloak_internal_url, s.client_id, s.client_secret)
    sessions = Sessions(clock)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["ts"] = _ts
    templates.env.globals.update(brand=s.brand_name, base=s.base_path, links=s.links(),
                                 states={"active": "active", "login_disabled": "suspended (mail still arrives)",
                                         "inactive": "suspended (mail refused)"})

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sessions = sessions
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret, session_cookie="admin_session",
                       max_age=SESSION_SECONDS, path=s.base_path or "/", same_site="strict",
                       https_only=s.secure_cookies)

    @app.middleware("http")
    async def gate_and_headers(request: Request, call_next):
        # Hide the console entirely from addresses outside ADMIN_ALLOWED_IPS
        # (the client address comes from mailcow's nginx via --proxy-headers).
        if s.allowed_ips and not request.url.path.endswith("/healthz"):
            try:
                addr = ipaddress.ip_address(request.client.host if request.client else "")
                allowed = any(addr in n for n in s.allowed_ips)
            except ValueError:
                allowed = False
            if not allowed:
                return HTMLResponse("Not found", status_code=404)
        resp: Response = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; "
                                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()")
        resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        if s.secure_cookies:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    r = APIRouter(prefix=s.base_path)

    def page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def problem(request: Request, status: int, title: str, message: str, retry: bool = True) -> HTMLResponse:
        return page(request, "message.html", status, title=title, message=message, retry=retry)

    def current(request: Request) -> AdminSession | None:
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

    def to_login() -> RedirectResponse:
        return RedirectResponse(f"{s.base_path}/login", status_code=303)

    def call(kind: str, u: AdminSession, method: str, path: str, **kw) -> tuple[int, dict]:
        """One call to the control plane or the sign-up app. (status, body); 0 if unreachable."""
        if kind == "cp":
            base, transport = s.control_plane_url, control_plane
            headers = {"Authorization": f"Bearer {s.control_plane_token}", "X-User-Token": u.access_token,
                       **kw.pop("headers", {})}
        else:
            base, transport = s.signup_url, signup
            headers = {"Authorization": f"Bearer {s.signup_token}", "X-Admin-Actor": u.sub[:64]}
        try:
            with httpx.Client(base_url=base, transport=transport, timeout=httpx.Timeout(60.0, connect=5.0)) as c:
                resp = c.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as e:
            log.warning("%s not answering: %s", kind, type(e).__name__)
            return 0, {"kind": "unavailable", "detail": "not answering"}
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 300:
            log.warning("%s refused %s %s: HTTP %s %s", kind, method, path, resp.status_code, body.get("kind", ""))
        return resp.status_code, body if isinstance(body, dict) else {}

    def need_csrf(u: AdminSession, token: str) -> bool:
        return bool(token) and secrets.compare_digest(token, u.csrf)

    def signed_out_by_cp(status: int, body: dict) -> bool:
        return status == 401 and body.get("kind") == "unauthorized"

    @app.get(f"{s.base_path}/healthz")
    def healthz():
        return {"ok": True}

    app.mount(f"{s.base_path}/static", StaticFiles(directory=str(HERE / "static")), name="static")

    # ---- sign-in -------------------------------------------------------------

    @r.get("/login")
    def login(request: Request):
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = _pkce()
        request.session.update({"state": state, "nonce": nonce, "pkce": verifier, "t": clock()})
        q = urlencode({"client_id": s.client_id, "response_type": "code", "scope": "openid profile",
                       "redirect_uri": s.redirect_uri, "state": state, "nonce": nonce,
                       "code_challenge": challenge, "code_challenge_method": "S256",
                       "prompt": "login", "max_age": "0"})
        return RedirectResponse(f"{s.oidc_issuer}/protocol/openid-connect/auth?{q}", status_code=303)

    @r.get("/callback")
    def callback(request: Request, code: str = "", state: str = "", error: str = ""):
        expected, nonce, verifier = (request.session.pop(k, None) for k in ("state", "nonce", "pkce"))
        started = request.session.pop("t", 0) or 0
        if error:
            return problem(request, 400, "Sign-in was cancelled", "Nothing was changed.")
        if not expected or not secrets.compare_digest(state, expected) or clock() - float(started) > 600:
            return problem(request, 400, "Sign-in expired", "That sign-in link is no longer valid. Please start again.")
        try:
            t = op.exchange(code, verifier, s.redirect_uri)
            claims = op.id_claims(t["id_token"], nonce)
        except OidcError as e:
            log.warning("sign-in failed: %s", e)
            return problem(request, 502, "Couldn't sign you in", "The sign-in service didn't answer as expected.")
        why = admin_problem(claims, s.admin_group, s.require_otp)
        if why:
            log.warning("refused console sign-in for %s: %s", claims.get("sub"), why)
            return problem(request, 403, "Not an administrator", why)
        now = clock()
        u = AdminSession(sub=str(claims["sub"]), name=str(claims.get("name") or claims.get("preferred_username", ""))[:100],
                         access_token=t["access_token"], access_until=now + int(t.get("expires_in", 60)),
                         refresh_token=t.get("refresh_token", ""), id_token=t["id_token"], started=now, seen=now,
                         csrf=secrets.token_urlsafe(24))
        sessions.drop(request.session.get("sid"))
        request.session.clear()  # new session: no fixation
        request.session["sid"] = sessions.put(u)
        return RedirectResponse(f"{s.base_path}/", status_code=303)

    @r.post("/logout")
    def logout(request: Request, csrf: str = Form("")):
        u = sessions.get(request.session.get("sid"))
        if u and not need_csrf(u, csrf):
            return RedirectResponse(f"{s.base_path}/", status_code=303)
        sessions.drop(request.session.pop("sid", None))
        request.session.clear()
        q = {"client_id": s.client_id, "post_logout_redirect_uri": f"{s.public_url}{s.base_path}/"}
        if u:
            q["id_token_hint"] = u.id_token
        return RedirectResponse(f"{s.oidc_issuer}/protocol/openid-connect/logout?{urlencode(q)}", status_code=303)

    # ---- invitations (the sign-up app's internal API) -------------------------

    def invitations_page(request: Request, u: AdminSession, status: int = 200, **ctx):
        code, data = call("signup", u, "GET", "/internal/v1/invitations")
        if code != 200:
            ctx.setdefault("error", "The sign-up app isn't answering; invitations can't be shown right now.")
            data = {}
        return page(request, "invitations.html", status, user=u, invites=data.get("invitations", []),
                    failures=data.get("failures", []), defaults=data.get("defaults", {}), tab="invitations", **ctx)

    @r.get("/")
    def home(request: Request):
        u = current(request)
        if not u:
            return to_login()
        return invitations_page(request, u, flash=request.session.pop("flash", None))

    @r.post("/invitations")
    def create_invitation(request: Request, quota_mb: int = Form(0), days: int = Form(0), note: str = Form(""),
                          csrf: str = Form("")):
        u = current(request)
        if not u:
            return to_login()
        if not need_csrf(u, csrf):
            return invitations_page(request, u, 400, error="The form expired; try again.")
        code, data = call("signup", u, "POST", "/internal/v1/invitations",
                          json={"quota_mb": quota_mb or None, "days": days or None, "note": note[:200]})
        if code != 201:
            return invitations_page(request, u, 400 if code else 502,
                                    error=data.get("detail") or "The invitation couldn't be created.")
        # Shown in this answer only (no redirect): the token is never kept here.
        return invitations_page(request, u, new_invite={"id": data["invitation"]["id"], "link": data["link"],
                                                        "code": data["token"],
                                                        "expires": data["invitation"]["expires_at"]})

    @r.post("/invitations/{inv_id}/{action}")
    def change_invitation(request: Request, inv_id: int, action: str, days: int = Form(7), csrf: str = Form("")):
        u = current(request)
        if not u:
            return to_login()
        if action not in ("revoke", "extend") or not need_csrf(u, csrf):
            request.session["flash"] = "The form expired; try again."
            return RedirectResponse(f"{s.base_path}/", status_code=303)
        code, data = call("signup", u, "POST", f"/internal/v1/invitations/{inv_id}/{action}",
                          json={"days": days} if action == "extend" else None)
        if code == 200:
            request.session["flash"] = (f"Invitation #{inv_id} revoked." if action == "revoke"
                                        else f"Invitation #{inv_id} now expires in {days} days.")
        else:
            why = data.get("detail") or "the sign-up app isn't answering"
            hint = " Someone may be signing up with it right now; try again in a few minutes." \
                if "in_progress" in why else ""
            verb = "revoked" if action == "revoke" else "extended"
            request.session["flash"] = f"Invitation #{inv_id} can't be {verb}: {why}.{hint}"
        return RedirectResponse(f"{s.base_path}/", status_code=303)

    # ---- control plane: accounts, trace, audit -------------------------------

    def cp_page(request: Request, u: AdminSession, name: str, path: str, tab: str, params: dict | None = None,
                **ctx):
        code, data = call("cp", u, "GET", path, params=params or {})
        if signed_out_by_cp(code, data):
            sessions.drop(request.session.pop("sid", None))
            return problem(request, 401, "Signed out", "The control plane didn't accept your sign-in. "
                                                       "Please sign in again with your one-time code.")
        error = None if code == 200 else (data.get("detail") or "The control plane isn't answering.")
        return page(request, name, 200 if code == 200 else 502, user=u, data=data, error=error, tab=tab, **ctx)

    @r.get("/accounts")
    def accounts(request: Request):
        u = current(request)
        if not u:
            return to_login()
        return cp_page(request, u, "accounts.html", "/v1/accounts", "accounts")

    def account_url(address: str) -> str:
        return f"{s.base_path}/accounts/{quote(address, safe='@')}"

    @r.get("/accounts/{address}")
    def account(request: Request, address: str):
        u = current(request)
        if not u:
            return to_login()
        # A fresh Idempotency-Key per page: a double-submitted form replays, it doesn't act twice.
        return cp_page(request, u, "account.html", f"/v1/accounts/{quote(address[:254], safe='@')}", "accounts",
                       flash=request.session.pop("flash", None), idem=secrets.token_urlsafe(24))

    # What each button on the account page does (PR 16, ADR 0021).
    LIFECYCLE = {
        "suspend": ("POST", "/suspend", "Suspended: nobody can sign in{mail}."),
        "reactivate": ("POST", "/reactivate", "Reactivated."),
        "quota": ("PUT", "/quota", "Quota set to {quota_mb} MB."),
        "password-reset": ("POST", "/password-reset", "A new password will be asked for at the next sign-in; "
                                                       "current sessions have ended."),
        "delete": ("POST", "/deletion", "Deletion scheduled for {due_at}. The account is suspended until then."),
        "cancel-delete": ("DELETE", "/deletion", "Deletion cancelled."),
    }

    @r.post("/accounts/{address}/{action}")
    def account_action(request: Request, address: str, action: str, csrf: str = Form(""), idem: str = Form(""),
                       stop_receiving: str = Form(""), quota_mb: int = Form(0), confirm: str = Form("")):
        u = current(request)
        if not u:
            return to_login()
        back = RedirectResponse(account_url(address), status_code=303)
        if action not in LIFECYCLE or not need_csrf(u, csrf) or not 16 <= len(idem) <= 128:
            request.session["flash"] = "The form expired; try again."
            return back
        if action == "delete" and confirm.strip().lower() != address.lower():
            request.session["flash"] = "Not scheduled: type the address exactly to confirm the deletion."
            return back
        method, suffix, done = LIFECYCLE[action]
        body = {"quota_mb": quota_mb} if action == "quota" else \
            {"stop_receiving": stop_receiving == "1"} if action == "suspend" else None
        code, data = call("cp", u, method, f"/v1/accounts/{quote(address[:254], safe='@')}{suffix}",
                          json=body, headers={"Idempotency-Key": idem})
        if signed_out_by_cp(code, data):
            sessions.drop(request.session.pop("sid", None))
            return to_login()
        if code in (200, 202):
            request.session["flash"] = done.format(
                mail="; mail no longer arrives" if data.get("state") == "inactive" else "; mail still arrives",
                quota_mb=data.get("quota_mb", ""), due_at=_ts(data.get("due_at", "")))
        else:
            request.session["flash"] = f"Not done: {data.get('detail') or 'the control plane is not answering'}"
        return back

    @r.get("/trace")
    def trace(request: Request, address: str = "", message_id: str = "", hours: int = 24):
        u = current(request)
        if not u:
            return to_login()
        form = {"address": address.strip()[:254], "message_id": message_id.strip()[:998], "hours": hours}
        if not (form["address"] or form["message_id"]):
            return page(request, "trace.html", user=u, data=None, error=None, form=form, tab="trace")
        params = {k: v for k, v in form.items() if v}
        return cp_page(request, u, "trace.html", "/v1/trace", "trace", params, form=form)

    @r.get("/audit")
    def audit(request: Request, action: str = "", outcome: str = ""):
        u = current(request)
        if not u:
            return to_login()
        params = {"limit": 200, **({"action": action[:64]} if action else {}),
                  **({"outcome": outcome} if outcome in ("ok", "refused", "failed") else {})}
        return cp_page(request, u, "audit.html", "/v1/audit", "audit", params, form={"action": action,
                                                                                    "outcome": outcome})

    app.include_router(r)
    return app


def get_app() -> FastAPI:  # uvicorn --factory app.main:get_app
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
