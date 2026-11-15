"""Invitation-based registration for mailcow.

Public pages:   {base}/            enter an invitation code
                {base}/i/{token}   invitation link -> registration form
                {base}/register    POST: create the mailbox
                {base}/check       GET: username availability (needs a valid invitation)
Health:         {base}/healthz
Internal API:   /internal/v1/...   invitations, for the admin console only (PR 15, ADR 0020)

The admin pages moved to the admin console (admin/, at /admin) in PR 15.
Invitations stay here: the console reaches them through the internal API,
outside {base}, so mailcow's nginx (which proxies only {base}/) never
forwards it, and only with its own token from its own address.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings, load_settings
from .db import Database
from .invites import Invitations
from .provisioning import BrokerClient, ProvisioningError
from .security import RateLimiter, csrf_ok, csrf_token, ip_allowed, parse_networks
from .validation import normalise_username, password_error, username_error

log = logging.getLogger("registration")
HERE = Path(__file__).parent
SESSION_SECONDS = 3600  # the cookie carries only the CSRF token
PRUNE_INTERVAL_SECONDS = 3600

INVITE_PROBLEMS = {
    "redeemed": "This invitation has already been used.",
    "revoked": "This invitation has been withdrawn.",
    "expired": "This invitation has expired. Ask the person who invited you for a new one.",
    "in_progress": "This invitation is being used right now. Try again in a few minutes.",
    None: "That invitation code isn't valid. Check the link or code you were given.",
}


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def create_app(settings: Settings | None = None, *, broker: BrokerClient | None = None,
               db: Database | None = None, limiter: RateLimiter | None = None,
               clock=time.time) -> FastAPI:
    s = settings or load_settings()
    db = db or Database(s.database_path, clock=clock)
    invites = Invitations(db)
    mc = broker or BrokerClient(s.broker_url, s.broker_token)
    rl = limiter or RateLimiter()
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["ts"] = _fmt_ts
    templates.env.globals.update(brand=s.brand_name, base=s.base_path, domain=s.mail_domain, webmail_url=s.webmail_url)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.invites, app.state.limiter = db, invites, rl
    app.add_middleware(
        SessionMiddleware, secret_key=s.session_secret, session_cookie="reg_session",
        max_age=SESSION_SECONDS, path=s.base_path or "/", same_site="lax", https_only=s.secure_cookies,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")  # invite tokens are in URLs
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), interest-cohort=()")
        if s.secure_cookies:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    r = APIRouter(prefix=s.base_path)

    def ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def page(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("csrf", csrf_token(request.session))
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def too_many(request: Request) -> HTMLResponse:
        return page(request, "message.html", 429, title="Too many attempts",
                    message="Too many attempts from your connection. Please wait a while and try again.")

    # ---- public ------------------------------------------------------------

    last_prune = [0.0]

    def maybe_prune() -> None:
        now = clock()
        if now - last_prune[0] >= PRUNE_INTERVAL_SECONDS:
            last_prune[0] = now
            db.prune(event_days=s.event_retention_days)

    @r.get("/healthz")
    def healthz():
        db.one("SELECT 1")
        maybe_prune()  # the container health check calls this every 30 s
        return {"ok": True}

    @r.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return page(request, "index.html")

    @r.post("/", response_class=HTMLResponse)
    def enter_code(request: Request, code: str = Form("")):
        code = code.strip()
        if "/i/" in code:  # someone pasted the whole link
            code = code.rsplit("/i/", 1)[1].split("?")[0].strip("/")
        return RedirectResponse(f"{s.base_path}/i/{code}", status_code=303) if code else page(
            request, "index.html", 400, error="Enter the invitation code you were given.")

    def show_form(request: Request, token: str, status: int = 200, **ctx):
        inv = invites.find(token)
        if not inv or not inv.usable:
            return page(request, "index.html", 404 if not inv else 410,
                        error=INVITE_PROBLEMS[inv.status if inv else None])
        return page(request, "register.html", status, token=token, invite=inv,
                    min_password=s.min_password_len, **ctx)

    @r.get("/i/{token}", response_class=HTMLResponse)
    def invite_link(request: Request, token: str):
        if not rl.hit("invite", ip(request), s.rl_invite_check_per_hour, 3600):
            return too_many(request)
        return show_form(request, token)

    @r.get("/check")
    def check_username(request: Request, u: str = ""):
        # Token comes in a header so it never appears in URLs or access logs.
        token = request.headers.get("x-invite-token", "")
        if not rl.hit("check", ip(request), s.rl_invite_check_per_hour * 4, 3600):
            return JSONResponse({"ok": False, "message": "Too many checks. Wait a while."}, 429)
        inv = invites.find(token)
        if not inv or not inv.usable:
            return JSONResponse({"ok": False, "message": "Invitation not valid."}, 403)
        name = normalise_username(u)
        err = username_error(name, reserved=s.reserved_usernames, min_len=s.min_username_len)
        if err:
            return {"ok": False, "message": err}
        try:
            taken = mc.address_taken(name)
        except ProvisioningError:
            return JSONResponse({"ok": False, "message": "Can't check right now."}, 503)
        return {"ok": not taken, "message": "That address is taken." if taken else f"{name}@{s.mail_domain} is available."}

    @r.post("/register", response_class=HTMLResponse)
    def register(request: Request, token: str = Form(""), username: str = Form(""), display_name: str = Form(""),
                 password: str = Form(""), password_confirm: str = Form(""), csrf: str = Form("")):
        client = ip(request)
        if not csrf_ok(request.session, csrf):
            return page(request, "message.html", 400, title="Form expired",
                        message="The form expired. Go back, reload the page and try again.")
        if not rl.hit("register", client, s.rl_register_per_hour, 3600):
            return too_many(request)

        name = normalise_username(username)
        display_name = "".join(" " if ch in "\r\n\t" else ch for ch in display_name
                               if ch in "\r\n\t" or unicodedata.category(ch)[0] != "C")
        display_name = " ".join(display_name.split())[:100]
        form = {"username": name, "display_name": display_name}

        err = username_error(name, reserved=s.reserved_usernames, min_len=s.min_username_len) or \
            password_error(password, password_confirm, username=name, min_len=s.min_password_len)
        if err:
            return show_form(request, token, 400, error=err, form=form)

        inv = invites.find(token)
        cid = invites.claim(inv.id) if inv else None
        if not cid:
            # re-read for an accurate message
            inv = invites.find(token)
            return page(request, "index.html", 409 if inv else 404,
                        error=INVITE_PROBLEMS[inv.status if inv and inv.status != "valid" else ("in_progress" if inv else None)])

        address = f"{name}@{s.mail_domain}"
        try:
            # The control plane checks the address is free, creates the account
            # (identity, then mailbox) and confirms it itself if a reply is lost.
            # One key per claim: a repeat of this exact attempt can't create twice.
            key = "claim-" + hashlib.sha256(cid.encode()).hexdigest()[:32]
            _, app_pw = mc.create_account(local_part=name, password=password, quota_mb=inv.quota_mb,
                                          name=display_name, invitation=f"inv-{inv.id}", idempotency_key=key)
        except ProvisioningError as e:
            if e.kind == "unavailable":
                # Our call to the broker failed; the mailbox may still have been created.
                try:
                    created = mc.mailbox_exists(name)
                except ProvisioningError:
                    created = False
                if created:
                    return finish(request, inv.id, cid, address, client, f"confirmed after error: {e.detail}")
            invites.release(inv.id, cid)
            if e.kind == "taken":
                return show_form(request, token, 409, error="That address is taken. Choose another username.", form=form)
            db.log("provision_failed", invitation_id=inv.id, address=address, detail=f"{e.kind}: {e.detail}", ip=client)
            log.warning("provisioning failed for %s: %s", address, e)
            if e.kind == "password":
                return show_form(request, token, 400, form=form,
                                 error="The mail server rejected that password. Try a longer one with mixed characters.")
            return page(request, "message.html", 503, title="Couldn't create your mailbox",
                        message="Something went wrong on our side. Your invitation has not been used; "
                                "please try again later.")

        return finish(request, inv.id, cid, address, client, app_password_required=app_pw)

    def finish(request: Request, inv_id: int, cid: str, address: str, client: str, detail: str = "",
               app_password_required: bool | None = None):
        # The mailbox exists at this point, so the user always sees success.
        if invites.redeem(inv_id, cid, address):
            db.log("registered", invitation_id=inv_id, address=address, detail=detail, ip=client)
        else:
            db.log("provision_failed", invitation_id=inv_id, address=address, ip=client,
                   detail="mailbox created, but the invitation was revoked or claimed again meanwhile; "
                          "check this mailbox in mailcow")
            log.warning("mailbox %s created without redeeming invitation #%s", address, inv_id)
        # None: the reply was lost, so we don't know; the page then explains app passwords.
        return page(request, "done.html", address=address, app_password_required=app_password_required)

    # ---- internal API for the admin console (PR 15, ADR 0020) ----------------

    console_nets = parse_networks(s.admin_console_clients)

    def console(request: Request) -> str | JSONResponse:
        """The admin console's own token from its own address, never through
        nginx. Returns who acted (the admin's Keycloak id, for the event log)."""
        refuse = JSONResponse({"ok": False, "kind": "forbidden", "detail": ""}, 403)
        if not s.admin_console_token or "x-forwarded-for" in request.headers:
            return refuse
        if not ip_allowed(ip(request), console_nets) or not console_nets:
            return refuse
        auth = request.headers.get("authorization", "")
        if not secrets.compare_digest(auth.encode(), f"Bearer {s.admin_console_token}".encode()):
            log.warning("internal API: wrong token from %s", ip(request))
            return JSONResponse({"ok": False, "kind": "unauthorized", "detail": ""}, 401)
        actor = request.headers.get("x-admin-actor", "")
        actor = "".join(ch for ch in actor if ch.isalnum() or ch in "-_.@")[:64]
        return f"admin:{actor or 'unknown'}"

    def public(inv) -> dict:
        return {"id": inv.id, "hint": inv.hint, "note": inv.note, "quota_mb": inv.quota_mb,
                "created_at": inv.created_at, "expires_at": inv.expires_at,
                "redeemed_address": inv.redeemed_address, "status": inv.status}

    def bad(detail: str, status: int = 422) -> JSONResponse:
        return JSONResponse({"ok": False, "kind": "invalid" if status == 422 else "conflict", "detail": detail},
                            status)

    @app.get("/internal/v1/invitations")
    def internal_list(request: Request):
        who = console(request)
        if isinstance(who, JSONResponse):
            return who
        failures = db.query("SELECT at, invitation_id, address, detail FROM events WHERE kind='provision_failed' "
                            "ORDER BY id DESC LIMIT 50")
        return {"ok": True, "invitations": [public(i) for i in invites.list()],
                "failures": [dict(f) for f in failures],
                "defaults": {"quota_mb": s.default_quota_mb, "max_quota_mb": s.max_quota_mb, "days": s.invite_days},
                "join_url": f"{s.public_url}{s.base_path}"}

    @app.post("/internal/v1/invitations", status_code=201)
    def internal_create(request: Request, body: dict = Body(...)):
        who = console(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            quota = int(body.get("quota_mb") or s.default_quota_mb)
            days = int(body.get("days") or s.invite_days)
        except (TypeError, ValueError):
            return bad("quota_mb and days must be numbers")
        note = str(body.get("note", ""))[:200]
        if not 1 <= quota <= s.max_quota_mb or not 1 <= days <= 90:
            return bad(f"quota must be 1-{s.max_quota_mb} MB and expiry 1-90 days")
        token, inv = invites.create(quota_mb=quota, days=days, note=note, ip=ip(request), by=who)
        # The secret token is in this one answer only; only its hash is stored.
        return {"ok": True, "token": token, "link": f"{s.public_url}{s.base_path}/i/{token}", "invitation": public(inv)}

    @app.post("/internal/v1/invitations/{inv_id}/revoke")
    def internal_revoke(request: Request, inv_id: int):
        who = console(request)
        if isinstance(who, JSONResponse):
            return who
        if not invites.revoke(inv_id, ip(request), by=who):
            inv = invites.get(inv_id)
            if inv is None:
                return JSONResponse({"ok": False, "kind": "not_found", "detail": ""}, 404)
            return bad(f"invitation is {inv.status}", 409)
        return {"ok": True, "invitation": public(invites.get(inv_id))}

    @app.post("/internal/v1/invitations/{inv_id}/extend")
    def internal_extend(request: Request, inv_id: int, body: dict = Body(...)):
        who = console(request)
        if isinstance(who, JSONResponse):
            return who
        try:
            ok = invites.extend(inv_id, int(body.get("days") or 0), ip(request), by=who)
        except (TypeError, ValueError):
            return bad("expiry must be 1-90 days")
        if not ok:
            inv = invites.get(inv_id)
            if inv is None:
                return JSONResponse({"ok": False, "kind": "not_found", "detail": ""}, 404)
            return bad(f"invitation is {inv.status}", 409)
        return {"ok": True, "invitation": public(invites.get(inv_id))}

    app.include_router(r)
    app.mount(f"{s.base_path}/static", StaticFiles(directory=str(HERE / "static")), name="static")
    if s.base_path:
        @app.get("/")
        def root_redirect():
            return RedirectResponse(f"{s.base_path}/", status_code=307)
    return app


def get_app() -> FastAPI:  # uvicorn --factory app.main:get_app
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
