"""Control-plane settings, read once from environment variables (see broker.env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Same list as the registration app: the broker enforces it too, so a
# compromised app can't register these.
DEFAULT_RESERVED = frozenset(
    """
    admin administrator postmaster abuse hostmaster webmaster support security mail root
    noreply no-reply donotreply do-not-reply mailer-daemon mailerdaemon nobody
    info help contact billing sales privacy legal dmarc dkim spf ssl-admin
    www ftp smtp imap pop pop3 autoconfig autodiscover sogo webmail api
    system operator owner staff team test tester sysadmin daemon
    """.split()
)

# Callers that can be configured, each with <NAME>_TOKEN and <NAME>_CLIENTS.
# What each may do is in capabilities.py (mirrors contracts/capabilities.yaml).
REQUIRED_CALLERS = ("signup",)
OPTIONAL_CALLERS = ("monitoring",)


@dataclass(frozen=True)
class Caller:
    name: str
    token: str
    clients: tuple[str, ...]  # source IPs/networks it may call from; ("*",) = any (tests only)


@dataclass(frozen=True)
class Settings:
    mailcow_api_url: str
    mailcow_api_key: str
    mailcow_tls_hostname: str | None
    mailcow_verify_tls: bool
    mail_domain: str
    callers: tuple[Caller, ...]
    db_path: str
    max_quota_mb: int
    min_username_len: int
    max_creates_per_day: int
    reserved: frozenset[str]


def load_settings(env: dict[str, str] | None = None) -> Settings:
    src = env if env is not None else os.environ

    def req(name: str) -> str:
        v = src.get(name, "").strip()
        if not v:
            raise RuntimeError(f"Missing required setting {name}")
        return v

    def num(name: str, default: int) -> int:
        v = src.get(name, "")
        return int(v) if v.strip() else default

    for old, new in (("BROKER_TOKEN", "SIGNUP_TOKEN"), ("BROKER_ALLOWED_CLIENTS", "SIGNUP_CLIENTS")):
        if src.get(old, "").strip():
            raise RuntimeError(f"{old} was renamed to {new} (one credential per caller); update broker.env")

    def caller(name: str, required: bool) -> Caller | None:
        key = name.upper()
        token = src.get(f"{key}_TOKEN", "").strip()
        if not token and not required:
            return None
        token = token or req(f"{key}_TOKEN")
        if len(token) < 32:
            raise RuntimeError(f"{key}_TOKEN must be at least 32 characters")
        clients = tuple(x.strip() for x in req(f"{key}_CLIENTS").split(",") if x.strip())
        return Caller(name, token, clients)

    callers = tuple(c for c in [caller(n, True) for n in REQUIRED_CALLERS] +
                    [caller(n, False) for n in OPTIONAL_CALLERS] if c)
    if len({c.token for c in callers}) != len(callers):
        raise RuntimeError("each caller needs its own token (two callers share one)")

    extra = {x.strip().lower() for x in src.get("EXTRA_RESERVED_USERNAMES", "").split(",") if x.strip()}
    return Settings(
        mailcow_api_url=req("MAILCOW_API_URL").rstrip("/"),
        mailcow_api_key=req("MAILCOW_API_KEY"),
        mailcow_tls_hostname=src.get("MAILCOW_TLS_HOSTNAME") or None,
        mailcow_verify_tls=src.get("MAILCOW_VERIFY_TLS", "true").strip().lower() in {"1", "true", "yes", "on"},
        mail_domain=req("MAIL_DOMAIN").lower(),
        callers=callers,
        db_path=src.get("CONTROL_PLANE_DB", "").strip() or "/data/control-plane.sqlite3",
        max_quota_mb=num("MAX_QUOTA_MB", 10240),
        min_username_len=num("MIN_USERNAME_LEN", 3),
        max_creates_per_day=num("MAX_CREATES_PER_DAY", 20),
        reserved=DEFAULT_RESERVED | extra,
    )
