"""Invitation-based registration for mailcow.

Public pages:   {base}/            enter an invitation code
                {base}/i/{token}   invitation link -> registration form
                {base}/register    POST: create the mailbox
                {base}/check       GET: username availability (needs a valid invitation)
Admin pages:    {base}/admin       invitations, registrations, failures
Health:         {base}/healthz
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings, load_settings
from .db import Database
from .invites import Invitations
from .mailcow import MailcowClient, MailcowError
from .security import RateLimiter, csrf_ok, csrf_token, verify_password, verify_totp
from .validation import normalise_username, password_error, username_error

log = logging.getLogger("registration")
HERE = Path(__file__).parent
ADMIN_SESSION_SECONDS = 8 * 3600
ADMIN_IDLE_SECONDS = 3600

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


def create_app(settings: Settings | None = None, *, mailcow: MailcowClient | None = None,
               db: Database | None = None, limiter: RateLimiter | None = None,
               clock=time.time) -> FastAPI:
    s = settings or load_settings()
    db = db or Database(s.database_path, clock=clock)
    invites = Invitations(db)
    mc = mailcow or MailcowClient(s.mailcow_api_url, s.mailcow_api_key, s.mailcow_tls_hostname, s.mailcow_verify_tls)
    rl = limiter or RateLimiter()
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["ts"] = _fmt_ts
    templates.env.globals.update(brand=s.brand_name, base=s.base_path, domain=s.mail_domain, webmail_url=s.webmail_url)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.invites, app.state.limiter = db, invites, rl
    app.add_middleware(
        SessionMiddleware, secret_key=s.session_secret, session_cookie="reg_session",
        max_age=ADMIN_SESSION_SECONDS, path=s.base_path or "/", same_site="lax", https_only=s.secure_cookies,
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

    @r.get("/healthz")
    def healthz():
        db.one("SELECT 1")
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
    def check_username(request: Request, token: str = "", u: str = ""):
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
            taken = mc.address_taken(f"{name}@{s.mail_domain}")
        except MailcowError:
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
        display_name = " ".join(display_name.split())[:100]
        form = {"username": name, "display_name": display_name}

        err = username_error(name, reserved=s.reserved_usernames, min_len=s.min_username_len) or \
            password_error(password, password_confirm, username=name, min_len=s.min_password_len)
        if err:
            return show_form(request, token, 400, error=err, form=form)

        inv = invites.find(token)
        if not inv or not invites.claim(inv.id):
            # re-read for an accurate message
            inv = invites.find(token)
            return page(request, "index.html", 409 if inv else 404,
                        error=INVITE_PROBLEMS[inv.status if inv and inv.status != "valid" else ("in_progress" if inv else None)])

        address = f"{name}@{s.mail_domain}"
        try:
            if mc.address_taken(address):
                invites.release(inv.id)
                return show_form(request, token, 409, error="That address is taken. Choose another username.", form=form)
            mc.add_mailbox(local_part=name, domain=s.mail_domain, password=password,
                           quota_mb=inv.quota_mb, name=display_name)
        except MailcowError as e:
            if e.kind == "unavailable":
                # The mailbox may have been created even though the reply was lost.
                try:
                    created = mc.mailbox_exists(address)
                except MailcowError:
                    created = False
                if created:
                    invites.redeem(inv.id, address)
                    db.log("registered", invitation_id=inv.id, address=address,
                           detail=f"confirmed after error: {e.detail}", ip=client)
                    return page(request, "done.html", address=address)
            invites.release(inv.id)
            db.log("provision_failed", invitation_id=inv.id, address=address, detail=f"{e.kind}: {e.detail}", ip=client)
            log.warning("provisioning failed for %s: %s", address, e)
            if e.kind == "taken":
                return show_form(request, token, 409, error="That address is taken. Choose another username.", form=form)
            if e.kind == "password":
                return show_form(request, token, 400, form=form,
                                 error="The mail server rejected that password. Try a longer one with mixed characters.")
            return page(request, "message.html", 503, title="Couldn't create your mailbox",
                        message="Something went wrong on our side. Your invitation has not been used; "
                                "please try again later.")

        invites.redeem(inv.id, address)
        db.log("registered", invitation_id=inv.id, address=address, ip=client)
        return page(request, "done.html", address=address)

    # ---- admin -------------------------------------------------------------

    def admin_ok(request: Request) -> bool:
        sess = request.session
        now = clock()
        if sess.get("admin") != s.admin_username:
            return False
        if now - sess.get("admin_at", 0) > ADMIN_SESSION_SECONDS or now - sess.get("admin_seen", 0) > ADMIN_IDLE_SECONDS:
            sess.clear()
            return False
        sess["admin_seen"] = int(now)
        return True

    def to_login():
        return RedirectResponse(f"{s.base_path}/admin/login", status_code=303)

    @r.get("/admin/login", response_class=HTMLResponse)
    def admin_login_form(request: Request):
        return page(request, "admin_login.html")

    @r.post("/admin/login", response_class=HTMLResponse)
    def admin_login(request: Request, username: str = Form(""), password: str = Form(""),
                    code: str = Form(""), csrf: str = Form("")):
        client = ip(request)
        if not csrf_ok(request.session, csrf):
            return page(request, "admin_login.html", 400, error="Form expired; try again.")
        if not rl.hit("login", client, s.rl_login_per_15min, 900):
            return page(request, "admin_login.html", 429, error="Too many attempts. Wait 15 minutes.")
        ok = (username == s.admin_username) & verify_password(s.admin_password_hash, password) \
            & verify_totp(s.admin_totp_secret, code, at=clock())
        if not ok:
            db.log("admin_login_failed", detail=f"user={username[:50]}", ip=client)
            return page(request, "admin_login.html", 401, error="Wrong username, password or code.")
        request.session.clear()
        now = int(clock())
        request.session.update(admin=s.admin_username, admin_at=now, admin_seen=now)
        db.log("admin_login", ip=client)
        return RedirectResponse(f"{s.base_path}/admin", status_code=303)

    @r.post("/admin/logout")
    def admin_logout(request: Request):
        request.session.clear()
        return to_login()

    @r.get("/admin", response_class=HTMLResponse)
    def admin_home(request: Request):
        if not admin_ok(request):
            return to_login()
        new = request.session.pop("new_invite", None)
        flash = request.session.pop("flash", None)
        failures = db.query("SELECT * FROM events WHERE kind='provision_failed' ORDER BY id DESC LIMIT 50")
        logins = db.query("SELECT * FROM events WHERE kind LIKE 'admin_login%' ORDER BY id DESC LIMIT 10")
        return page(request, "admin.html", invites=invites.list(), failures=failures, logins=logins,
                    new_invite=new, flash=flash, default_quota=s.default_quota_mb,
                    max_quota=s.max_quota_mb, default_days=s.invite_days)

    @r.post("/admin/invites")
    def admin_create(request: Request, quota_mb: int = Form(0), days: int = Form(0), note: str = Form(""),
                     csrf: str = Form("")):
        if not admin_ok(request):
            return to_login()
        if not csrf_ok(request.session, csrf):
            request.session["flash"] = "Form expired; try again."
            return RedirectResponse(f"{s.base_path}/admin", status_code=303)
        quota = quota_mb or s.default_quota_mb
        days = days or s.invite_days
        if not (1 <= quota <= s.max_quota_mb) or not (1 <= days <= 90):
            request.session["flash"] = f"Quota must be 1–{s.max_quota_mb} MB and expiry 1–90 days."
            return RedirectResponse(f"{s.base_path}/admin", status_code=303)
        token, inv = invites.create(quota_mb=quota, days=days, note=note, ip=ip(request))
        request.session["new_invite"] = {"id": inv.id, "link": f"{s.public_url}{s.base_path}/i/{token}",
                                         "code": token, "expires": inv.expires_at}
        return RedirectResponse(f"{s.base_path}/admin", status_code=303)

    @r.post("/admin/invites/{inv_id}/revoke")
    def admin_revoke(request: Request, inv_id: int, csrf: str = Form("")):
        if not admin_ok(request):
            return to_login()
        if csrf_ok(request.session, csrf):
            request.session["flash"] = (f"Invitation #{inv_id} revoked." if invites.revoke(inv_id, ip(request))
                                        else f"Invitation #{inv_id} can't be revoked.")
        return RedirectResponse(f"{s.base_path}/admin", status_code=303)

    @r.post("/admin/invites/{inv_id}/extend")
    def admin_extend(request: Request, inv_id: int, days: int = Form(7), csrf: str = Form("")):
        if not admin_ok(request):
            return to_login()
        if csrf_ok(request.session, csrf):
            try:
                ok = invites.extend(inv_id, days, ip(request))
            except ValueError as e:
                request.session["flash"] = str(e)
            else:
                request.session["flash"] = (f"Invitation #{inv_id} now expires in {days} days." if ok
                                            else f"Invitation #{inv_id} can't be extended.")
        return RedirectResponse(f"{s.base_path}/admin", status_code=303)

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
