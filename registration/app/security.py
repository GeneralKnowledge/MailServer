"""Rate limiting, CSRF tokens and IP allow-lists. (The admin login moved to the admin console in PR 15.)"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import defaultdict, deque


# ---- rate limiting --------------------------------------------------------

class RateLimiter:
    """In-memory sliding window per (bucket, key). The app runs as one process."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, bucket: str, key: str, limit: int, window: float) -> bool:
        """Record a hit; return False if the limit is already reached."""
        if limit <= 0:
            return True
        now = self.clock()
        with self._lock:
            q = self._hits[(bucket, key)]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # crude memory bound
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# ---- CSRF -----------------------------------------------------------------

def csrf_token(session: dict) -> str:
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        session["csrf"] = tok
    return tok


def csrf_ok(session: dict, submitted: str | None) -> bool:
    expected = session.get("csrf")
    return bool(expected and submitted and hmac.compare_digest(expected, submitted))


# ---- client IP allow-list ---------------------------------------------------

def parse_networks(spec: str):
    import ipaddress
    nets = []
    for part in (spec or "").split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


def ip_allowed(ip: str, networks) -> bool:
    """Empty list = everyone allowed."""
    if not networks:
        return True
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in networks)
