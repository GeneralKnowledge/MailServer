"""Account lifecycle from the server's shell (PR 16, ADR 0021): break-glass,
for when the admin console (which needs Keycloak) isn't there.

    docker compose exec broker python -m broker.account show sam@mail.avedeus.ovh
    docker compose exec broker python -m broker.account suspend sam@mail.avedeus.ovh [--stop-receiving]
    docker compose exec broker python -m broker.account reactivate | password-reset | delete | cancel-delete <address>
    docker compose exec broker python -m broker.account quota <address> <MB>

It calls the control plane inside its own container (127.0.0.1) with
ADMIN_TOKEN, so every change is audited as actor `admin-break-glass`, and
goes through exactly the same checks as the console. `delete` schedules the
deletion (grace period, newer backup); it never deletes at once.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from urllib.parse import quote

import httpx

BASE = "http://127.0.0.1:8100/v1/accounts/"
ACTIONS = {
    "show": ("GET", ""),
    "suspend": ("POST", "/suspend"),
    "reactivate": ("POST", "/reactivate"),
    "quota": ("PUT", "/quota"),
    "password-reset": ("POST", "/password-reset"),
    "delete": ("POST", "/deletion"),
    "cancel-delete": ("DELETE", "/deletion"),
}


def main(argv: list[str] | None = None, client: httpx.Client | None = None, out=print) -> int:
    p = argparse.ArgumentParser(prog="python -m broker.account", description="Account lifecycle (admin, break-glass).")
    p.add_argument("action", choices=sorted(ACTIONS))
    p.add_argument("address")
    p.add_argument("quota_mb", nargs="?", type=int, help="for quota: the new size in MB")
    p.add_argument("--stop-receiving", action="store_true", help="for suspend: refuse new mail too")
    a = p.parse_args(argv)
    token = os.environ.get("ADMIN_TOKEN", "").strip()
    if not token:
        out("ADMIN_TOKEN is not set in broker.env; account lifecycle is admin-only.")
        return 2
    if a.action == "quota" and not a.quota_mb:
        out("quota needs the new size in MB, e.g. quota sam@mail.avedeus.ovh 4096")
        return 2
    method, suffix = ACTIONS[a.action]
    body = {"quota_mb": a.quota_mb} if a.action == "quota" else \
        {"stop_receiving": True} if a.action == "suspend" and a.stop_receiving else None
    headers = {"Authorization": f"Bearer {token}"}
    if method != "GET":
        headers["Idempotency-Key"] = secrets.token_hex(16)
    c = client or httpx.Client()
    try:
        r = c.request(method, BASE + quote(a.address.strip(), safe="@") + suffix, json=body, headers=headers,
                      timeout=60)
    except httpx.HTTPError as e:
        out(f"control plane not answering: {type(e).__name__}")
        return 2
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 300:
        out(f"refused: HTTP {r.status_code} {data.get('kind', '')} {data.get('detail', '')}".rstrip())
        return 1
    if a.action == "show":
        data.pop("ok", None)
        history = data.pop("history", [])
        out(json.dumps(data, indent=2))
        for e in history[:20]:
            who = (e.get("actor") or {}).get("id") or e.get("caller")
            out(f"  {e.get('time')}  {e.get('action'):<28} {e.get('outcome'):<8} {who}")
    else:
        out(json.dumps({k: v for k, v in data.items() if k != "ok"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
