"""Invitation-based registration for mailcow.

Public pages:   {base}/            enter an invitation code
                {base}/i/{token}   invitation link -> registration form
                {base}/register    POST: create the mailbox
                {base}/check       GET: username availability (needs a valid invitation)
Admin pages:    {base}/admin       invitations, registrations, failures
Health:         {base}/healthz
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import unicodedata
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
from .provisioning import BrokerClient, ProvisioningError
from .security import (RateLimiter, csrf_ok, csrf_token, ip_allowed, parse_networks, totp_step,
                       verify_password)
from .validation import normalise_username, password_error, username_error

log = logging.getLogger("registration")
HERE = Path(__file__).parent
ADMIN_SESSION_SECONDS = 8 * 3600
ADMIN_IDLE_SECONDS = 3600
NEW_INVITE_REVEAL_SECONDS = 300  # a newly created invite link is shown once, within this time
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
            db.prune(event_days=s.event_retention_days, admin_session_seconds=ADMIN_SESSION_SECONDS)

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

    # ---- admin -------------------------------------------------------------

    admin_nets = parse_networks(s.admin_allowed_ips)

    @app.middleware("http")
    async def admin_ip_gate(request: Request, call_next):
        # Hide the admin pages entirely from addresses outside ADMIN_ALLOWED_IPS.
        if request.url.path.startswith(f"{s.base_path}/admin") and not ip_allowed(ip(request), admin_nets):
            return HTMLResponse("Not found", status_code=404)
        return await call_next(request)

    def _sid_hash(sid: str) -> str:
        return hashlib.sha256(sid.encode()).hexdigest()

    def admin_ok(request: Request) -> bool:
        sid = request.session.get("admin_sid")
        if not sid:
            return False
        now = int(clock())
        h = _sid_hash(sid)
        row = db.one("SELECT * FROM admin_sessions WHERE id_hash=?", (h,))
        if not row or now - row["created_at"] > ADMIN_SESSION_SECONDS or now - row["seen_at"] > ADMIN_IDLE_SECONDS:
            if row:
                db.execute("DELETE FROM admin_sessions WHERE id_hash=?", (h,))
            request.session.clear()
            return False
        db.execute("UPDATE admin_sessions SET seen_at=? WHERE id_hash=?", (now, h))
        return True

    # A newly created invitation's token is shown on the next admin page view
    # only. It is held here in memory (one process by design), keyed by the
    # admin session, never in the cookie: Starlette sessions are signed, not
    # encrypted.
    new_invites: dict[str, tuple[float, dict]] = {}

    def stash_new_invite(request: Request, info: dict) -> None:
        now = clock()
        for k in [k for k, (at, _) in new_invites.items() if now - at > NEW_INVITE_REVEAL_SECONDS]:
            del new_invites[k]
        new_invites[_sid_hash(request.session["admin_sid"])] = (now, info)

    def take_new_invite(request: Request) -> dict | None:
        item = new_invites.pop(_sid_hash(request.session.get("admin_sid", "")), None)
        if item and clock() - item[0] <= NEW_INVITE_REVEAL_SECONDS:
            return item[1]
        return None

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
        if not rl.hit("login", client, s.rl_login_per_15min, 900) or \
                not rl.hit("login-all", "*", s.rl_login_global_per_hour, 3600):
            db.log("admin_login_failed", detail="rate limited", ip=client)
            return page(request, "admin_login.html", 429, error="Too many attempts. Wait 15 minutes.")
        user_ok = secrets.compare_digest(username.encode(), s.admin_username.encode())
        pw_ok = verify_password(s.admin_password_hash, password)
        step = totp_step(s.admin_totp_secret, code, at=clock())
        last = db.one("SELECT value FROM state WHERE key='totp_last_step'")
        replay = step is not None and last is not None and step <= int(last["value"])
        if not (user_ok and pw_ok and step is not None) or replay:
            db.log("admin_login_failed", detail=f"user={username[:50]}" + (" (code reused)" if replay else ""), ip=client)
            return page(request, "admin_login.html", 401, error="Wrong username, password or code.")
        db.execute("INSERT INTO state(key, value) VALUES ('totp_last_step', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(step),))
        now = int(clock())
        db.prune(event_days=s.event_retention_days, admin_session_seconds=ADMIN_SESSION_SECONDS)
        sid = secrets.token_urlsafe(32)
        db.execute("INSERT INTO admin_sessions(id_hash, created_at, seen_at, ip) VALUES (?,?,?,?)",
                   (_sid_hash(sid), now, now, client))
        request.session.clear()  # new session: no fixation
        request.session["admin_sid"] = sid
        db.log("admin_login", ip=client)
        return RedirectResponse(f"{s.base_path}/admin", status_code=303)

    @r.post("/admin/logout")
    def admin_logout(request: Request, csrf: str = Form("")):
        sid = request.session.get("admin_sid")
        if sid and csrf_ok(request.session, csrf):
            db.execute("DELETE FROM admin_sessions WHERE id_hash=?", (_sid_hash(sid),))
            request.session.clear()
        return to_login()

    @r.post("/admin/logout-all")
    def admin_logout_all(request: Request, csrf: str = Form("")):
        if admin_ok(request) and csrf_ok(request.session, csrf):
            db.execute("DELETE FROM admin_sessions")
            db.log("admin_logout_all", ip=ip(request))
            request.session.clear()
        return to_login()

    @r.get("/admin", response_class=HTMLResponse)
    def admin_home(request: Request):
        if not admin_ok(request):
            return to_login()
        new = take_new_invite(request)
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
        stash_new_invite(request, {"id": inv.id, "link": f"{s.public_url}{s.base_path}/i/{token}",
                                   "code": token, "expires": inv.expires_at})
        return RedirectResponse(f"{s.base_path}/admin", status_code=303)

    @r.post("/admin/invites/{inv_id}/revoke")
    def admin_revoke(request: Request, inv_id: int, csrf: str = Form("")):
        if not admin_ok(request):
            return to_login()
        if csrf_ok(request.session, csrf):
            request.session["flash"] = (f"Invitation #{inv_id} revoked." if invites.revoke(inv_id, ip(request))
                                        else f"Invitation #{inv_id} can't be revoked: it has been used, "
                                             "or someone is signing up with it right now (try again in a few minutes).")
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
