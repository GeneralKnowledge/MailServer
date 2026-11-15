"""Health probe for scripts/health-check.sh (PR 13).

Runs inside the broker container and calls the control plane's own
GET /v1/health over HTTP as the `monitoring` caller, so every health check
also exercises the real path: token, source address and capability check.

    docker compose exec -T broker python -m broker.probe

Prints one line per component, tab-separated: <component> <status> <detail>.
Exit 0: all ok. Exit 1: something degraded or down. Exit 2: the probe itself
could not get an answer (control plane not answering, monitoring caller not
configured or refused).
"""

from __future__ import annotations

import os
import sys

import httpx

URL = "http://127.0.0.1:8100/v1/health"


def run(client: httpx.Client, token: str, out=print) -> int:
    if not token:
        out("probe\tdown\tMONITORING_TOKEN is not set in broker.env")
        return 2
    try:
        r = client.get(URL, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except httpx.HTTPError as e:
        out(f"probe\tdown\tcontrol plane not answering: {type(e).__name__}")
        return 2
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200:
        out(f"probe\tdown\tcontrol plane refused the monitoring caller: HTTP {r.status_code} {data.get('kind', '')}")
        return 2
    components = data.get("components") or {}
    for name in sorted(components):
        c = components[name]
        detail = " ".join(str(c.get("detail", "")).split())[:200]
        out(f"{name}\t{c.get('status', 'down')}\t{detail}")
    return 0 if data.get("status") == "ok" else 1


def main() -> int:
    with httpx.Client() as client:
        return run(client, os.environ.get("MONITORING_TOKEN", "").strip())


if __name__ == "__main__":
    sys.exit(main())
