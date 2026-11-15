"""Broker settings, read once from environment variables (see broker.env.example)."""

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


@dataclass(frozen=True)
class Settings:
    mailcow_api_url: str
    mailcow_api_key: str
    mailcow_tls_hostname: str | None
    mailcow_verify_tls: bool
    mail_domain: str
    token: str                      # shared secret the registration app presents
    allowed_clients: tuple[str, ...]  # client IPs allowed to call the broker; ("*",) = any (tests only)
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

    token = req("BROKER_TOKEN")
    if len(token) < 32:
        raise RuntimeError("BROKER_TOKEN must be at least 32 characters")
    clients = tuple(x.strip() for x in req("BROKER_ALLOWED_CLIENTS").split(",") if x.strip())
    extra = {x.strip().lower() for x in src.get("EXTRA_RESERVED_USERNAMES", "").split(",") if x.strip()}
    return Settings(
        mailcow_api_url=req("MAILCOW_API_URL").rstrip("/"),
        mailcow_api_key=req("MAILCOW_API_KEY"),
        mailcow_tls_hostname=src.get("MAILCOW_TLS_HOSTNAME") or None,
        mailcow_verify_tls=src.get("MAILCOW_VERIFY_TLS", "true").strip().lower() in {"1", "true", "yes", "on"},
        mail_domain=req("MAIL_DOMAIN").lower(),
        token=token,
        allowed_clients=clients,
        max_quota_mb=num("MAX_QUOTA_MB", 10240),
        min_username_len=num("MIN_USERNAME_LEN", 3),
        max_creates_per_day=num("MAX_CREATES_PER_DAY", 20),
        reserved=DEFAULT_RESERVED | extra,
    )
