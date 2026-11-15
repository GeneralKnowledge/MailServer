"""Provisioning broker: the only component that holds the mailcow API key.

The public registration app calls this service instead of mailcow. It can
only ask two things, and the broker checks every request against its own
rules, whatever the caller sends:

    POST /v1/addresses/check   is <local_part>@<domain> free?
    POST /v1/mailboxes         create <local_part>@<domain>

Anything else mailcow's API can do (change or delete other mailboxes, add
aliases or forwards, admins, domains, DKIM, settings) is simply not offered.
If the registration app were compromised, the worst it could do through the
broker is create a limited number of new mailboxes on the one domain.

Also:  GET /healthz       liveness, no upstream call, no auth
       GET /v1/upstream   checks the mailcow key and allow-list (for health-check.sh)

Callers must come from BROKER_ALLOWED_CLIENTS and present
"Authorization: Bearer <BROKER_TOKEN>". No port is published; the broker is
reachable only on the internal avedeus-edge network.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import threading
import time
from collections import deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings, load_settings
from .mailcow import MailcowClient, MailcowError
from .rules import local_part_error

log = logging.getLogger("broker")
DAY = 86400


class CheckRequest(BaseModel):
    local_part: str = Field(max_length=64)


class CreateRequest(BaseModel):
    local_part: str = Field(max_length=64)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=100)
    quota_mb: int


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
               clock=time.time) -> FastAPI:
    s = settings or load_settings()
    mc = mailcow or MailcowClient(s.mailcow_api_url, s.mailcow_api_key, s.mailcow_tls_hostname, s.mailcow_verify_tls)
    created: deque[float] = deque()      # times of successful creations, for the daily cap
    create_lock = threading.Lock()        # one creation at a time: keeps the cap and checks exact

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.created = created

    @app.middleware("http")
    async def gate(request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        host = request.client.host if request.client else ""
        if not _client_allowed(host, s.allowed_clients):
            log.warning("refused client %s", host)
            return _err(403, "forbidden", "client not allowed")
        auth = request.headers.get("authorization", "")
        if not (auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].encode(), s.token.encode())):
            log.warning("bad token from %s", host)
            return _err(401, "unauthorized", "missing or wrong token")
        return await call_next(request)

    def address(local: str) -> str:
        return f"{local}@{s.mail_domain}"

    def upstream_error(e: MailcowError) -> JSONResponse:
        return _err(502, e.kind, e.detail)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/v1/upstream")
    def upstream():
        try:
            return {"ok": True, "mailcow_version": mc.version()}
        except MailcowError as e:
            return upstream_error(e)

    @app.post("/v1/addresses/check")
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
            return upstream_error(e)
        return {"ok": True, "valid": True, "reason": None, "taken": taken, "mailbox": mailbox}

    @app.post("/v1/mailboxes")
    def create(body: CreateRequest):
        local = body.local_part.strip().lower()
        err = local_part_error(local, reserved=s.reserved, min_len=s.min_username_len)
        if err:
            return _err(422, "invalid", f"local part: {err}")
        if not 1 <= body.quota_mb <= s.max_quota_mb:
            return _err(422, "invalid", f"quota must be 1-{s.max_quota_mb} MB")
        name = "".join(ch if ch.isprintable() else (" " if ch in "\r\n\t" else "") for ch in body.name)
        name = " ".join(name.split())[:100]
        addr = address(local)
        with create_lock:
            now = clock()
            while created and created[0] <= now - DAY:
                created.popleft()
            if len(created) >= s.max_creates_per_day:
                log.warning("daily limit reached, refused %s", addr)
                return _err(429, "limit", f"more than {s.max_creates_per_day} new mailboxes in 24 h")
            try:
                if mc.address_taken(addr):
                    return _err(409, "taken", addr)
                mc.add_mailbox(local_part=local, domain=s.mail_domain, password=body.password,
                               quota_mb=body.quota_mb, name=name)
            except MailcowError as e:
                if e.kind == "unavailable":
                    # The mailbox may exist even though mailcow's reply was lost.
                    try:
                        exists = mc.mailbox_exists(addr)
                    except MailcowError:
                        exists = False
                    if exists:
                        created.append(now)
                        log.info("created %s (confirmed after error: %s)", addr, e.detail)
                        return JSONResponse({"ok": True, "address": addr}, status_code=201)
                log.warning("create %s failed: %s", addr, e)
                if e.kind == "taken":
                    return _err(409, "taken", e.detail)
                if e.kind == "password":
                    return _err(422, "password", e.detail)
                return upstream_error(e)
            created.append(now)
        log.info("created %s quota=%sMB", addr, body.quota_mb)
        return JSONResponse({"ok": True, "address": addr}, status_code=201)

    return app


def get_app() -> FastAPI:  # uvicorn --factory broker.main:get_app
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
