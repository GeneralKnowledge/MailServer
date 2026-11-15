"""Client for the control plane (broker/ in this repo), as its `signup` caller.

The registration app never talks to mailcow and never holds its API key.
It can only ask the control plane whether an address is free and to create
a mailbox; the control plane applies its own rules and limits.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


class ProvisioningError(Exception):
    """kind: 'taken' | 'password' | 'invalid' | 'limit' | 'auth' | 'unavailable' | 'rejected'"""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class Availability:
    valid: bool
    reason: str | None
    taken: bool
    mailbox: bool


@dataclass
class BrokerClient:
    base_url: str
    token: str
    timeout: float = 50.0  # the broker makes up to four 10 s mailcow calls; nginx gives up at 60 s
    transport: httpx.BaseTransport | None = None  # injected in tests

    def _request(self, method: str, path: str, **kw) -> dict:
        try:
            with httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(self.timeout, connect=5.0),
                              headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                              transport=self.transport, follow_redirects=False) as c:
                r = c.request(method, path, **kw)
        except httpx.TimeoutException as e:
            raise ProvisioningError("unavailable", f"broker timeout: {e}") from e
        except httpx.HTTPError as e:
            raise ProvisioningError("unavailable", f"broker connection error: {e}") from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if r.status_code < 300:
            return data
        if r.status_code in (401, 403):
            kind = "auth"
            detail = f"broker refused us (HTTP {r.status_code}): {data.get('detail', '')}"
        elif r.status_code == 422 and "kind" not in data:
            # Not the control plane's envelope: don't keep its body, which may echo
            # what we sent (password included) and would end up in our event log.
            kind, detail = "invalid", "request refused (HTTP 422)"
        else:
            kind = str(data.get("kind") or "unavailable")
            detail = str(data.get("detail") or f"HTTP {r.status_code}")
        raise ProvisioningError(kind, detail)

    def check(self, local_part: str) -> Availability:
        d = self._request("POST", "/v1/addresses/check", json={"local_part": local_part})
        return Availability(valid=bool(d.get("valid")), reason=d.get("reason"),
                            taken=bool(d.get("taken")), mailbox=bool(d.get("mailbox")))

    def address_taken(self, local_part: str) -> bool:
        a = self.check(local_part)
        return a.taken or not a.valid

    def mailbox_exists(self, local_part: str) -> bool:
        return self.check(local_part).mailbox

    def create_mailbox(self, *, local_part: str, password: str, quota_mb: int, name: str = "",
                       idempotency_key: str | None = None) -> str:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        d = self._request("POST", "/v1/mailboxes", headers=headers, json={
            "local_part": local_part, "password": password, "quota_mb": quota_mb, "name": name})
        return str(d.get("address", ""))
