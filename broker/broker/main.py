"""Avedeus control plane (grown from the audit-7 provisioning broker).

The only component that holds the mailcow API key (ADR 0003). It stays a
narrow security and integration broker (ADR 0017): caller authentication and
capabilities, typed adapter operations, an append-only audit log,
idempotency keys and health. No workflow engine, event bus or plugins.

    GET  /healthz              liveness, public
    GET  /v1/health            control plane + adapter health     (monitoring)
    GET  /v1/upstream          the mailcow key and allow-list work (monitoring)
    POST /v1/addresses/check   is <local_part>@<domain> free?      (signup)
    POST /v1/accounts          create an account: identity, then mailbox (signup; PR 12)
    POST /v1/mailboxes         create <local_part>@<domain>        (signup; superseded by /v1/accounts)

Every caller has its own token and source addresses (config.py), and may use
only the operations in capabilities.py. Every state-changing call and every
refused call writes one audit event. Errors are always
{"ok": false, "kind": ..., "detail": ...} and never echo the request.
No port is published; it is reachable only on the internal avedeus-edge network.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import secrets
import threading
import time
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .capabilities import CAPABILITIES, PUBLIC
from .config import Caller, Settings, load_settings
from .keycloak import KeycloakClient, KeycloakError
from .mailcow import MailcowClient, MailcowError
from .rules import local_part_error
from .store import Store

log = logging.getLogger("broker")
DAY = 86400
IDEMPOTENCY_HEADER = "idempotency-key"
SLOW_UPSTREAM_MS = 5000  # an answer slower than this marks the adapter degraded


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_part: str = Field(max_length=64)


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_part: str = Field(max_length=64)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=100)
    quota_mb: int


class AccountRequest(CreateRequest):
    # Opaque reference to the sign-up app's invitation, recorded in the audit
    # log. Never the invitation's secret token.
    invitation: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class Refused(Exception):
    """Raised to answer with the error envelope (and, if audited, after writing the event)."""

    def __init__(self, status: int, kind: str, detail: str = ""):
        super().__init__(detail)
        self.status, self.kind, self.detail = status, kind, detail


def _err(status: int, kind: str, detail: str = "") -> JSONResponse:
    return JSONResponse({"ok": False, "kind": kind, "detail": detail[:500]}, status_code=status)


def _client_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    if "*" in allowed:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(a, strict=False) for a in allowed)


def create_app(settings: Settings | None = None, *, mailcow: MailcowClient | None = None,
               store: Store | None = None, keycloak: KeycloakClient | None = None, clock=time.time) -> FastAPI:
    s = settings or load_settings()
    mc = mailcow or MailcowClient(s.mailcow_api_url, s.mailcow_api_key, s.mailcow_tls_hostname, s.mailcow_verify_tls)
    db = store or Store(s.db_path, clock=clock)
    kc: KeycloakClient | None = None
    if s.identity_provider == "keycloak":
        kc = keycloak or KeycloakClient(s.keycloak_url, s.keycloak_realm, s.keycloak_client_id,
                                        s.keycloak_client_secret)
    adapters = [mc] + ([kc] if kc else [])
    create_lock = threading.Lock()  # one creation at a time: keeps the cap and checks exact

    def host_of(request: Request) -> str:
        return request.client.host if request.client else ""

    def audit(request: Request, **kw) -> None:
        """Audit write that never turns a finished upstream change into an error."""
        caller = getattr(request.state, "caller", None)
        try:
            db.audit(caller=caller.name if caller else "unknown", source_ip=host_of(request) or None,
                     request_id=getattr(request.state, "request_id", ""), **kw)
        except Exception:  # noqa: BLE001
            log.exception("could not write audit event %s", kw.get("action"))

    def authorize(request: Request) -> None:
        """Runs before every route: who is calling, and may they use this operation?"""
        request.state.request_id = uuid.uuid4().hex[:16]
        request.state.caller = None
        op = request.scope["route"].operation_id
        if op in PUBLIC:
            return
        host = host_of(request)
        auth = request.headers.get("authorization", "")
        presented = auth[7:].encode() if auth.startswith("Bearer ") else b""
        caller: Caller | None = None
        for c in s.callers:  # compare against every token, so timing doesn't reveal which matched
            if hmac.compare_digest(presented, c.token.encode()) and caller is None:
                caller = c
        if caller is None:
            log.warning("bad token from %s for %s", host, op)
            audit(request, action="auth.refused", outcome="refused", operation=op, error_kind="unauthorized")
            raise Refused(401, "unauthorized", "missing or wrong token")
        request.state.caller = caller
        if not _client_allowed(host, caller.clients):
            log.warning("caller %s from disallowed address %s", caller.name, host)
            audit(request, action="auth.refused", outcome="refused", operation=op, error_kind="forbidden")
            raise Refused(403, "forbidden", "client not allowed")
        if op not in CAPABILITIES.get(caller.name, frozenset()):
            log.warning("caller %s may not use %s", caller.name, op)
            audit(request, action="auth.refused", outcome="refused", operation=op, error_kind="forbidden")
            raise Refused(403, "forbidden", f"{caller.name} may not use {op}")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, dependencies=[Depends(authorize)])
    app.state.store = db

    @app.exception_handler(Refused)
    async def refused(request: Request, e: Refused):
        return _err(e.status, e.kind, e.detail)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, e: RequestValidationError):
        # Never echo the request: FastAPI's default puts the input (password included) in the reply.
        fields = sorted({".".join(str(p) for p in err.get("loc", ())[1:]) or "body" for err in e.errors()})
        audit(request, action="request.invalid", outcome="refused",
              operation=getattr(request.scope.get("route"), "operation_id", None), error_kind="invalid")
        return _err(422, "invalid", "invalid or missing fields: " + ", ".join(fields))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException):
        return _err(e.status_code, "not_found" if e.status_code in (404, 405) else "invalid", "")

    def address(local: str) -> str:
        return f"{local}@{s.mail_domain}"

    # ---- health -------------------------------------------------------------

    @app.get("/healthz", operation_id="healthz")
    def healthz():
        return {"ok": True}

    @app.get("/v1/health", operation_id="health")
    def health():
        components = {}
        for a in adapters:
            h = a.health().as_dict()
            if h["status"] == "ok" and h.get("latency_ms", 0) > SLOW_UPSTREAM_MS:
                h = {**h, "status": "degraded", "detail": f"slow: {h['latency_ms']} ms. {h.get('detail', '')}".strip()}
            components[a.name] = h
        components["control_plane_db"] = db.health()
        states = {c["status"] for c in components.values()}
        overall = "down" if "down" in states else "degraded" if "degraded" in states else "ok"
        now = clock()
        checked = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        return {"status": overall, "checked_at": checked, "components": components}

    @app.get("/v1/upstream", operation_id="upstreamCheck")
    def upstream():
        try:
            return {"ok": True, "mailcow_version": mc.version()}
        except MailcowError as e:
            return _err(502, e.kind, e.detail)

    # ---- signup -------------------------------------------------------------

    @app.post("/v1/addresses/check", operation_id="checkAddress")
    def check(body: CheckRequest):
        local = body.local_part.strip().lower()
        err = local_part_error(local, reserved=s.reserved, min_len=s.min_username_len)
        if err:
            return {"ok": True, "valid": False, "reason": err, "taken": False, "mailbox": False}
        addr = address(local)
        try:
            mailbox = mc.mailbox_exists(addr)
            taken = mailbox or mc.alias_exists(addr)
        except MailcowError as e:
            return _err(502, e.kind, e.detail)
        return {"ok": True, "valid": True, "reason": None, "taken": taken, "mailbox": mailbox}

    @app.post("/v1/accounts", operation_id="createAccount")
    def create_account(body: AccountRequest, request: Request):
        return provision(body, request, operation="createAccount", action="account.create",
                         invitation=body.invitation)

    @app.post("/v1/mailboxes", operation_id="createMailbox")
    def create_mailbox(body: CreateRequest, request: Request):
        if kc:
            # With one identity, a mailbox without its Keycloak user would be
            # an account nobody can sign in to. Only createAccount may create.
            audit(request, action="mailbox.create", outcome="refused", error_kind="conflict",
                  operation="createMailbox", address=address(body.local_part.strip().lower()))
            return _err(409, "conflict", "the identity provider is on: use createAccount (POST /v1/accounts)")
        return provision(body, request, operation="createMailbox", action="mailbox.create")

    def provision(body: CreateRequest, request: Request, *, operation: str, action: str,
                  invitation: str | None = None):
        """Validate, apply limits and idempotency, then create (identity first, then mailbox)."""
        caller: Caller = request.state.caller
        key = request.headers.get(IDEMPOTENCY_HEADER) or None
        local = body.local_part.strip().lower()
        addr = address(local)
        ctx = {"operation": operation, "address": addr, "adapter": kc.name if kc else mc.name,
               "idempotency_key": key}
        changes = {"quota_mb": {"from": None, "to": body.quota_mb}}
        if invitation:
            changes["invitation"] = {"from": None, "to": invitation}

        def refuse(status: int, kind: str, detail: str, outcome: str = "refused") -> JSONResponse:
            audit(request, action=action, outcome=outcome, error_kind=kind, **ctx)
            return _err(status, kind, detail)

        if key is None and operation == "createAccount":
            return refuse(422, "invalid", "Idempotency-Key is required")
        if key is not None and not 16 <= len(key) <= 128:
            return refuse(422, "invalid", "Idempotency-Key must be 16-128 characters")
        err = local_part_error(local, reserved=s.reserved, min_len=s.min_username_len)
        if err:
            return refuse(422, "invalid", f"local part: {err}")
        if not 1 <= body.quota_mb <= s.max_quota_mb:
            return refuse(422, "invalid", f"quota must be 1-{s.max_quota_mb} MB")
        name = "".join(ch if ch.isprintable() else (" " if ch in "\r\n\t" else "") for ch in body.name)
        name = " ".join(name.split())[:100]

        if key:
            fp = Store.fingerprint({**body.model_dump(), "local_part": local})
            prior = db.idem_begin(caller.name, key, operation, fp)
            if prior and prior[0] == "replay":
                return JSONResponse(prior[2], status_code=prior[1])
            if prior:
                return refuse(409, "conflict", prior[1])

        # Tells the sign-up page what to say about mail apps: with one identity
        # they need an app password (ADR 0015); without it, the account password works.
        result = {"ok": True, "address": addr}
        if operation == "createAccount":
            result["app_password_required"] = kc is not None

        def done(status: int, payload: dict, outcome: str = "ok", detail: str = "") -> JSONResponse:
            audit(request, action=action, outcome=outcome, changes=changes, **ctx)
            if key:
                db.idem_finish(caller.name, key, status, payload)
            log.info("created %s quota=%sMB%s", addr, body.quota_mb, f" ({detail})" if detail else "")
            return JSONResponse(payload, status_code=status)

        def created_today() -> int:
            since = clock() - DAY
            return db.count("mailbox.create", "ok", since) + db.count("account.create", "ok", since)

        def add_mailbox(password: str, authsource: str) -> JSONResponse | None:
            """Create the mailbox; None if mailcow definitively refused (Refused raised)."""
            try:
                mc.add_mailbox(local_part=local, domain=s.mail_domain, password=password,
                               quota_mb=body.quota_mb, name=name, authsource=authsource)
            except MailcowError as e:
                if e.kind == "unavailable":
                    # The mailbox may exist even though mailcow's reply was lost.
                    try:
                        exists = mc.mailbox_exists(addr)
                    except MailcowError:
                        exists = False
                    if exists:
                        return done(201, result, detail=f"confirmed after error: {e.detail}")
                log.warning("create %s failed: %s", addr, e)
                if e.kind == "taken":
                    raise Refused(409, "taken", e.detail) from e
                if e.kind == "password":
                    raise Refused(422, "password", e.detail) from e
                raise Refused(502, e.kind, e.detail) from e
            return done(201, result)

        try:
            with create_lock:
                # The daily cap counts successful creations in the audit log, so it survives restarts.
                if created_today() >= s.max_creates_per_day:
                    log.warning("daily limit reached, refused %s", addr)
                    raise Refused(429, "limit", f"more than {s.max_creates_per_day} new mailboxes in 24 h")
                try:
                    if mc.address_taken(addr):
                        raise Refused(409, "taken", addr)
                except MailcowError as e:
                    raise Refused(502, e.kind, e.detail) from e
                if not kc:
                    return add_mailbox(body.password, "mailcow")
                return with_identity(local, addr, name, body.password, add_mailbox)
        except Refused as e:
            if key:
                db.idem_abandon(caller.name, key)
            return refuse(e.status, e.kind, e.detail, "failed" if e.status >= 500 else "refused")
        except Exception:
            if key:
                db.idem_abandon(caller.name, key)
            raise

    def with_identity(local: str, addr: str, name: str, password: str, add_mailbox):
        """Keycloak user first, then the mailbox (PR 12). The user's password goes
        to Keycloak only; the mailbox gets a random one nobody knows."""
        assert kc is not None
        try:
            existing = kc.find_user(local)
            linked = db.link_get(addr, kc.name)
            if existing and existing != linked:
                # Someone else's identity (or one made outside this flow): never adopt it.
                raise Refused(409, "taken", f"{addr} (identity exists)")
            created_now = existing is None
            attrs = {"mailcow_template": s.mailbox_template} if s.mailbox_template else None
            uid = existing
            if not uid:
                try:
                    uid = kc.create_user(username=local, email=addr, password=password, name=name,
                                         group=s.keycloak_group, attributes=attrs)
                except KeycloakError as e:
                    # A lost reply: we checked there was no such user a moment
                    # ago under the creation lock, so one there now is ours.
                    if e.kind != "unavailable" or not (uid := kc.find_user(local)):
                        raise
        except KeycloakError as e:
            log.warning("identity for %s failed: %s", addr, e)
            if e.kind == "taken":
                raise Refused(409, "taken", e.detail) from e
            if e.kind == "password":
                raise Refused(422, "password", e.detail) from e
            raise Refused(502, e.kind, e.detail) from e
        # Recorded before the mailbox call, so a retry after a lost reply
        # recognises this identity as ours and carries on.
        db.link_set(addr, kc.name, uid)
        try:
            return add_mailbox(secrets.token_urlsafe(36), "keycloak")
        except Refused as e:
            if e.status < 500 and created_now:
                # mailcow definitively refused: undo, so nothing is half-made.
                try:
                    kc.delete_user(uid)
                    db.link_delete(addr, kc.name)
                except KeycloakError as ke:
                    log.error("could not remove Keycloak user for %s after refusal: %s", addr, ke)
            raise

    # Every route must name its operation, or authorize() can't check it.
    for route in app.routes:
        op = getattr(route, "operation_id", "missing")
        if hasattr(route, "endpoint") and route.path not in ("/openapi.json",) and not op:
            raise RuntimeError(f"route {route.path} has no operation_id")

    return app


def get_app() -> FastAPI:  # uvicorn --factory broker.main:get_app
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
