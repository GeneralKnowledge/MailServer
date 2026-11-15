"""Minimal client for the mailcow REST API (/api/v1).

Only three calls are used: look up a mailbox, list aliases, add a mailbox.
The API key stays on the server; it is never sent to browsers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

log = logging.getLogger("registration.mailcow")


class MailcowError(Exception):
    """kind: 'taken' | 'password' | 'auth' | 'unavailable' | 'rejected'"""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class MailcowClient:
    base_url: str
    api_key: str
    tls_hostname: str | None = None  # when base_url is the internal nginx-mailcow container
    verify_tls: bool = True
    # A sign-up makes up to four calls; 4 x 10 s stays inside nginx's 60 s proxy timeout.
    timeout: float = 10.0
    transport: httpx.BaseTransport | None = None  # injected in tests

    def _client(self) -> httpx.Client:
        headers = {"X-API-Key": self.api_key, "Accept": "application/json"}
        if self.tls_hostname:
            headers["Host"] = self.tls_hostname
        return httpx.Client(
            base_url=self.base_url, headers=headers, timeout=httpx.Timeout(self.timeout, connect=5.0),
            verify=self.verify_tls, transport=self.transport, follow_redirects=False,
        )

    def _ext(self) -> dict:
        # Verify the certificate for the public hostname even when connecting
        # to the container name on the internal Docker network.
        return {"sni_hostname": self.tls_hostname} if self.tls_hostname else {}

    def _request(self, method: str, path: str, **kw):
        try:
            with self._client() as c:
                r = c.request(method, path, extensions=self._ext(), **kw)
        except httpx.TimeoutException as e:
            raise MailcowError("unavailable", f"timeout: {e}") from e
        except httpx.HTTPError as e:
            raise MailcowError("unavailable", f"connection error: {e}") from e
        if r.status_code in (401, 403):
            raise MailcowError("auth", f"HTTP {r.status_code}: API key rejected or source IP not allowed")
        if r.status_code >= 400:
            raise MailcowError("unavailable", f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError as e:
            raise MailcowError("unavailable", f"non-JSON response: {r.text[:300]}") from e

    # ---- reads -----------------------------------------------------------

    def mailbox_exists(self, address: str) -> bool:
        data = self._request("GET", f"/api/v1/get/mailbox/{quote(address, safe='@')}")
        # mailcow returns {} (or []) for an unknown mailbox.
        if isinstance(data, dict):
            return bool(data) and data.get("username", "").lower() == address.lower()
        if isinstance(data, list):
            return any(isinstance(m, dict) and m.get("username", "").lower() == address.lower() for m in data)
        return False

    def alias_exists(self, address: str) -> bool:
        data = self._request("GET", "/api/v1/get/alias/all")
        if not isinstance(data, list):
            return False
        a = address.lower()
        return any(isinstance(x, dict) and str(x.get("address", "")).lower() == a for x in data)

    def address_taken(self, address: str) -> bool:
        return self.mailbox_exists(address) or self.alias_exists(address)

    # ---- write -----------------------------------------------------------

    def add_mailbox(self, *, local_part: str, domain: str, password: str, quota_mb: int, name: str = "") -> None:
        payload = {
            "local_part": local_part,
            "domain": domain,
            "name": name or local_part,
            "quota": str(quota_mb),
            "password": password,
            "password2": password,
            "active": "1",
            "force_pw_update": "0",
            "tls_enforce_in": "1",
            "tls_enforce_out": "1",
            "authsource": "mailcow",
        }
        data = self._request("POST", "/api/v1/add/mailbox", json=payload)
        results = data if isinstance(data, list) else [data]
        for item in results:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "success":
                return
            if item.get("type") in ("danger", "error"):
                msg = str(item.get("msg", ""))
                low = msg.lower()
                if "exists" in low or "is_alias" in low or "taken" in low:
                    raise MailcowError("taken", msg)
                if "password" in low:
                    raise MailcowError("password", msg)
                raise MailcowError("rejected", msg)
        raise MailcowError("unavailable", f"unexpected response: {str(data)[:300]}")
