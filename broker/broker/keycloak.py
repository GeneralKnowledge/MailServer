"""The Keycloak adapter (PR 12): the control plane's only way into Keycloak.

It signs in as the realm's `avedeus-control-plane` client (client
credentials; realm-as-code in infrastructure/identity) and offers typed
operations only: find a user, create an invited user with a password and a
group, delete a user this flow just created, and health. Never a pass-through
to Keycloak's admin API (ADR 0009, docs/integration-contract.md).

Keycloak is the browser identity only. Mail clients use mailcow app
passwords (ADR 0015); nothing here checks or syncs a mail password.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from .adapters import Health

log = logging.getLogger("broker.keycloak")


class KeycloakError(Exception):
    """kind: 'taken' | 'password' | 'auth' | 'unavailable' | 'rejected'"""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class KeycloakClient:
    name = "keycloak"
    base_url: str          # e.g. http://keycloak:8080/auth (on avedeus-edge, not through nginx)
    realm: str
    client_id: str
    client_secret: str
    timeout: float = 10.0
    transport: httpx.BaseTransport | None = None  # injected in tests
    clock: object = time.monotonic
    _token: str | None = field(default=None, repr=False)
    _token_until: float = field(default=0.0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url.rstrip("/"), timeout=httpx.Timeout(self.timeout, connect=5.0),
                            transport=self.transport, follow_redirects=False,
                            headers={"Accept": "application/json"})

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            with self._client() as c:
                return c.request(method, path, **kw)
        except httpx.TimeoutException as e:
            raise KeycloakError("unavailable", f"timeout: {e}") from e
        except httpx.HTTPError as e:
            raise KeycloakError("unavailable", f"connection error: {e}") from e

    def _access_token(self) -> str:
        with self._lock:
            if self._token and self.clock() < self._token_until:
                return self._token
            r = self._send("POST", f"/realms/{quote(self.realm)}/protocol/openid-connect/token", data={
                "grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret})
            if r.status_code in (400, 401):
                raise KeycloakError("auth", f"HTTP {r.status_code}: client credentials refused")
            if r.status_code >= 300:
                raise KeycloakError("unavailable", f"token endpoint HTTP {r.status_code}")
            try:
                data = r.json()
                token, ttl = str(data["access_token"]), int(data.get("expires_in", 60))
            except (ValueError, KeyError, TypeError) as e:
                raise KeycloakError("unavailable", "token endpoint gave no token") from e
            # Renew a little early so a token never expires mid-request.
            self._token, self._token_until = token, self.clock() + max(ttl - 30, 5)
            return token

    def _admin(self, method: str, path: str, **kw) -> httpx.Response:
        url = f"/admin/realms/{quote(self.realm)}{path}"
        r = self._send(method, url, headers={"Authorization": f"Bearer {self._access_token()}"}, **kw)
        if r.status_code == 401:  # token revoked or expired early: one fresh try
            with self._lock:
                self._token = None
            r = self._send(method, url, headers={"Authorization": f"Bearer {self._access_token()}"}, **kw)
        if r.status_code in (401, 403):
            raise KeycloakError("auth", f"HTTP {r.status_code}: the control plane's client lacks a right it needs")
        return r

    # ---- reads -----------------------------------------------------------

    def health(self) -> Health:
        start = time.monotonic()
        try:
            r = self._admin("GET", "/users/count")
            if r.status_code != 200:
                raise KeycloakError("unavailable", f"HTTP {r.status_code}")
        except KeycloakError as e:
            return Health("down", f"{e.kind}: {e.detail}")
        return Health("ok", f"realm {self.realm}", int((time.monotonic() - start) * 1000))

    def find_user(self, username: str) -> str | None:
        """The Keycloak id of the user with exactly this username, or None."""
        r = self._admin("GET", "/users", params={"username": username, "exact": "true", "briefRepresentation": "true"})
        if r.status_code != 200:
            raise KeycloakError("unavailable", f"user lookup HTTP {r.status_code}")
        try:
            users = r.json()
        except ValueError as e:
            raise KeycloakError("unavailable", "user lookup: non-JSON response") from e
        for u in users if isinstance(users, list) else []:
            if isinstance(u, dict) and str(u.get("username", "")).lower() == username.lower():
                return str(u["id"])
        return None

    # ---- writes ----------------------------------------------------------

    def create_user(self, *, username: str, email: str, password: str, name: str = "",
                    group: str = "users", attributes: dict[str, str] | None = None) -> str:
        """Create an enabled user in `group` with a permanent password; returns its id."""
        first, _, last = (name or "").partition(" ")
        body = {
            "username": username,
            "email": email,
            "emailVerified": True,   # the address is the mailbox being created with it
            "enabled": True,
            "firstName": first,
            "lastName": last,
            "groups": [f"/{group}"],
            "attributes": {k: [v] for k, v in (attributes or {}).items()},
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        }
        r = self._admin("POST", "/users", json=body)
        if r.status_code == 201:
            loc = r.headers.get("location", "")
            if "/users/" in loc:
                return loc.rsplit("/", 1)[1]
            uid = self.find_user(username)
            if uid:
                return uid
            raise KeycloakError("unavailable", "user created but its id could not be read")
        if r.status_code == 409:
            raise KeycloakError("taken", f"a Keycloak user named {username} exists")
        if r.status_code == 400:
            msg = _error_text(r)
            if "password" in msg.lower() or "invalidPassword" in msg:
                raise KeycloakError("password", msg)
            raise KeycloakError("rejected", msg)
        raise KeycloakError("unavailable", f"create user HTTP {r.status_code}")

    def delete_user(self, user_id: str) -> None:
        """Undo a creation in this same flow (mailbox creation was definitively refused)."""
        r = self._admin("DELETE", f"/users/{quote(user_id)}")
        if r.status_code not in (204, 404):
            raise KeycloakError("unavailable", f"delete user HTTP {r.status_code}")


def _error_text(r: httpx.Response) -> str:
    try:
        d = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    if isinstance(d, dict):
        return str(d.get("errorMessage") or d.get("error_description") or d.get("error") or f"HTTP {r.status_code}")[:300]
    return f"HTTP {r.status_code}"
