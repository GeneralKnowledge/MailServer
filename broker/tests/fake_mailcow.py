"""A stand-in for mailcow's /api/v1 that keeps mailboxes in memory.

Response shapes follow mailcow's API: errors come back as HTTP 200 with
{"type": "danger", "msg": [...]} entries.
"""

import json

import httpx

API_KEY = "test-api-key"
RO_API_KEY = "test-read-only-key"   # mailcow's read-only key: GET only


class FakeMailcow:
    def __init__(self):
        self.mailboxes: dict[str, dict] = {}
        self.aliases: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self.fail_next_add: str | None = None  # None | "timeout" | "timeout_after_create" | "http500" | "danger"
        self.down = False
        self.on_add = None  # optional callable, run when add/mailbox is called (simulates slow requests)
        # PR 34: what the message trace reads. Entries in mailcow's API shapes.
        self.logs: dict[str, list[dict]] = {"postfix": [], "rspamd-history": [], "dovecot": []}
        self.quarantine: list[dict] = []
        self.mailq: list[dict] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        key = request.headers.get("X-API-Key")
        if key == RO_API_KEY and request.method != "GET":
            return httpx.Response(403, json={"type": "error", "msg": "read-only key"})
        if key not in (API_KEY, RO_API_KEY):
            return httpx.Response(401, json={"type": "error", "msg": "authentication failed"})

        if request.method == "GET" and path.startswith("/api/v1/get/logs/"):
            kind, count = path.split("/")[-2:]
            return httpx.Response(200, json=list(reversed(self.logs.get(kind, [])))[: int(count)])
        if request.method == "GET" and path == "/api/v1/get/quarantine/all":
            return httpx.Response(200, json=self.quarantine)
        if request.method == "GET" and path == "/api/v1/get/mailq/all":
            return httpx.Response(200, json=self.mailq)

        if request.method == "GET" and path == "/api/v1/get/mailbox/all":
            return httpx.Response(200, json=list(self.mailboxes.values()) or {})
        if request.method == "GET" and path.startswith("/api/v1/get/mailbox/"):
            addr = path.rsplit("/", 1)[1].lower()
            mb = self.mailboxes.get(addr)
            return httpx.Response(200, json=mb or {})
        if request.method == "GET" and path == "/api/v1/get/status/version":
            return httpx.Response(200, json={"version": "2026-11"})
        if request.method == "GET" and path == "/api/v1/get/alias/all":
            return httpx.Response(200, json=[{"id": n, "address": a, "goto": "x@y"} for n, a in enumerate(self.aliases)])
        if request.method == "POST" and path == "/api/v1/add/mailbox":
            body = json.loads(request.content)
            addr = f"{body['local_part']}@{body['domain']}".lower()
            mode, self.fail_next_add = self.fail_next_add, None
            if self.on_add:
                self.on_add()
            if mode == "timeout":
                raise httpx.ReadTimeout("timed out", request=request)
            if mode == "http500":
                return httpx.Response(500, text="Internal Server Error")
            if mode == "danger":
                return httpx.Response(200, json=[{"type": "danger", "log": [], "msg": ["mailbox_quota_left_exceeded", "2048"]}])
            if addr in self.mailboxes:
                return httpx.Response(200, json=[{"type": "danger", "log": [], "msg": ["object_exists", addr]}])
            if addr in self.aliases:
                return httpx.Response(200, json=[{"type": "danger", "log": [], "msg": ["is_alias", addr]}])
            if body["password"] != body["password2"] or len(body["password"]) < 6:
                return httpx.Response(200, json=[{"type": "danger", "log": [], "msg": "password_complexity"}])
            self.mailboxes[addr] = {"username": addr, "name": body["name"], "quota": int(body["quota"]) * 1048576,
                                    "active": int(body["active"]), "_password": body["password"]}
            if mode == "timeout_after_create":
                raise httpx.ReadTimeout("timed out after commit", request=request)
            return httpx.Response(200, json=[{"type": "success", "log": [], "msg": ["mailbox_added", addr]}])
        return httpx.Response(404, json={"type": "error", "msg": "route not found"})
