"""A stand-in for Keycloak's token endpoint and the slice of its admin API
the control plane's Keycloak adapter uses. Users are kept in memory.

Shapes follow Keycloak 26: 201 + Location on create, 409 for a duplicate
username, 400 {"error": ..., "errorMessage": ...} for a password-policy refusal.
"""

import json
import uuid
from urllib.parse import parse_qs

import httpx

REALM = "avedeus"
CLIENT_ID = "avedeus-control-plane"
CLIENT_SECRET = "k" * 40


class FakeKeycloak:
    def __init__(self):
        self.users: dict[str, dict] = {}   # id -> representation (credentials kept for assertions)
        self.calls: list[tuple[str, str]] = []
        self.down = False
        self.issued = 0
        self.min_password = 12
        self.fail_next_create: str | None = None   # None | "timeout" | "http500"
        self.fail_next_update = False
        self.logouts: list[str] = []
        self.events: list[dict] = []        # PR 26: LOGIN events, any order
        self.events_forbidden = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def by_username(self, username):
        return next((u for u in self.users.values() if u["username"] == username.lower()), None)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        if path == f"/auth/realms/{REALM}/protocol/openid-connect/token":
            form = parse_qs(request.content.decode())
            if form.get("client_id") != [CLIENT_ID] or form.get("client_secret") != [CLIENT_SECRET]:
                return httpx.Response(401, json={"error": "unauthorized_client"})
            self.issued += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.issued}", "expires_in": 300})
        admin = f"/auth/admin/realms/{REALM}"
        if not path.startswith(admin):
            return httpx.Response(404)
        if not request.headers.get("authorization", "").startswith("Bearer tok-"):
            return httpx.Response(401, json={"error": "HTTP 401 Unauthorized"})
        rest = path[len(admin):]
        if request.method == "GET" and rest == "/users/count":
            return httpx.Response(200, json=len(self.users))
        if request.method == "GET" and rest == "/users":
            q = request.url.params.get("username", "").lower()
            return httpx.Response(200, json=[{"id": u["id"], "username": u["username"]}
                                             for u in self.users.values() if u["username"] == q])
        if request.method == "POST" and rest == "/users":
            body = json.loads(request.content)
            mode, self.fail_next_create = self.fail_next_create, None
            if mode == "http500":
                return httpx.Response(500, text="boom")
            if self.by_username(body["username"]):
                return httpx.Response(409, json={"errorMessage": "User exists with same username"})
            pw = body["credentials"][0]["value"]
            if len(pw) < self.min_password:
                return httpx.Response(400, json={"error": "invalidPasswordMinLengthMessage",
                                                 "errorMessage": f"Invalid password: minimum length {self.min_password}."})
            uid = str(uuid.uuid4())
            self.users[uid] = {**body, "id": uid, "username": body["username"].lower()}
            if mode == "timeout":
                raise httpx.ReadTimeout("timed out after commit", request=request)
            return httpx.Response(201, headers={"Location": f"http://keycloak:8080{admin}/users/{uid}"})
        # PR 26: sign-in events, newest first, paged.
        if request.method == "GET" and rest == "/events":
            if self.events_forbidden:
                return httpx.Response(403, json={"error": "HTTP 403 Forbidden"})
            p = request.url.params
            evs = sorted((e for e in self.events if e["type"] in p.get_list("type")),
                         key=lambda e: -e["time"])
            first, mx = int(p.get("first", 0)), int(p.get("max", 100))
            return httpx.Response(200, json=evs[first:first + mx])
        # PR 16: lifecycle.
        if rest.startswith("/users/") and rest.endswith("/logout") and request.method == "POST":
            uid = rest.split("/")[2]
            if uid not in self.users:
                return httpx.Response(404, json={"error": "User not found"})
            self.logouts.append(uid)
            return httpx.Response(204)
        if request.method == "GET" and rest.startswith("/users/") and rest.count("/") == 2:
            u = self.users.get(rest.rsplit("/", 1)[1])
            if u is None:
                return httpx.Response(404, json={"error": "User not found"})
            return httpx.Response(200, json={"id": u["id"], "username": u["username"],
                                             "enabled": u.get("enabled", True),
                                             "requiredActions": u.get("requiredActions", [])})
        if request.method == "PUT" and rest.startswith("/users/") and rest.count("/") == 2:
            if self.fail_next_update:
                self.fail_next_update = False
                return httpx.Response(500, text="boom")
            u = self.users.get(rest.rsplit("/", 1)[1])
            if u is None:
                return httpx.Response(404, json={"error": "User not found"})
            body = json.loads(request.content)
            u.update({k: v for k, v in body.items() if k in ("enabled", "requiredActions")})
            return httpx.Response(204)
        if request.method == "DELETE" and rest.startswith("/users/"):
            uid = rest.rsplit("/", 1)[1]
            return httpx.Response(204 if self.users.pop(uid, None) else 404)
        return httpx.Response(404)
