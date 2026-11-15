"""Settings, read once from environment variables (see .env.example)."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass, field

# Names nobody may register. The first block is required by the project brief;
# the rest are role/system names that should stay with the operator.
DEFAULT_RESERVED = frozenset(
    """
    admin administrator postmaster abuse hostmaster webmaster support security mail root
    noreply no-reply donotreply do-not-reply mailer-daemon mailerdaemon nobody
    info help contact billing sales privacy legal dmarc dkim spf ssl-admin
    www ftp smtp imap pop pop3 autoconfig autodiscover sogo webmail api
    system operator owner staff team test tester sysadmin daemon
    """.split()
)


def _bool(src, name: str, default: bool) -> bool:
    v = src.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(src, name: str, default: int) -> int:
    v = src.get(name)
    return int(v) if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # mailcow API
    mailcow_api_url: str
    mailcow_api_key: str
    mailcow_tls_hostname: str | None  # SNI/Host override when calling nginx-mailcow internally
    mailcow_verify_tls: bool
    mail_domain: str
    webmail_url: str
    public_url: str  # where this app is reached, e.g. https://mail.example.com (base_path is appended)

    brand_name: str

    # app
    base_path: str
    database_path: str
    session_secret: str
    secure_cookies: bool

    # admin login
    admin_username: str
    admin_password_hash: str
    admin_totp_secret: str
    admin_allowed_ips: str  # comma-separated IPs/CIDRs allowed to reach /admin; empty = anyone

    # defaults
    default_quota_mb: int
    max_quota_mb: int
    invite_days: int
    min_username_len: int
    min_password_len: int
    reserved_usernames: frozenset[str] = field(default_factory=lambda: DEFAULT_RESERVED)

    # rate limits (per client IP)
    rl_register_per_hour: int = 10
    rl_invite_check_per_hour: int = 30
    rl_login_per_15min: int = 5
    rl_login_global_per_hour: int = 30  # all IPs together; stops slow distributed guessing

    # event log rows (which hold client IPs) older than this are deleted; 0 = keep forever
    event_retention_days: int = 180


def _check_totp_secret(secret: str) -> str:
    """Fail at start-up, not at the first login, if the TOTP secret is unusable."""
    s = secret.strip().replace(" ", "").upper()
    try:
        key = base64.b32decode(s + "=" * (-len(s) % 8))
    except (binascii.Error, ValueError):
        raise RuntimeError("ADMIN_TOTP_SECRET is not valid base32; create one with: python -m app.cli new-totp")
    if len(key) < 16:
        raise RuntimeError("ADMIN_TOTP_SECRET is too short (need at least 128 bits); use: python -m app.cli new-totp")
    return s


def load_settings(env: dict[str, str] | None = None) -> Settings:
    src = env if env is not None else os.environ

    def req(name: str) -> str:
        v = src.get(name, "").strip()
        if not v:
            raise RuntimeError(f"Missing required setting {name}")
        return v

    domain = req("MAIL_DOMAIN").lower()
    extra_reserved = {
        x.strip().lower() for x in src.get("EXTRA_RESERVED_USERNAMES", "").split(",") if x.strip()
    }
    base_path = "/" + src.get("BASE_PATH", "/join").strip("/")
    if base_path == "/":
        base_path = ""

    secret = req("SESSION_SECRET")
    if len(secret) < 32:
        raise RuntimeError("SESSION_SECRET must be at least 32 characters")

    return Settings(
        mailcow_api_url=req("MAILCOW_API_URL").rstrip("/"),
        mailcow_api_key=req("MAILCOW_API_KEY"),
        mailcow_tls_hostname=src.get("MAILCOW_TLS_HOSTNAME") or None,
        mailcow_verify_tls=_bool(src, "MAILCOW_VERIFY_TLS", True),
        mail_domain=domain,
        webmail_url=src.get("WEBMAIL_URL", f"https://mail.{domain}").rstrip("/"),
        public_url=src.get("PUBLIC_URL", src.get("WEBMAIL_URL", f"https://mail.{domain}")).rstrip("/"),
        brand_name=src.get("BRAND_NAME", "Avedeus Mail"),
        base_path=base_path,
        database_path=src.get("DATABASE_PATH", "/data/registration.sqlite3"),
        session_secret=secret,
        secure_cookies=_bool(src, "SECURE_COOKIES", True),
        admin_username=req("ADMIN_USERNAME"),
        admin_password_hash=req("ADMIN_PASSWORD_HASH"),
        admin_totp_secret=_check_totp_secret(req("ADMIN_TOTP_SECRET")),
        admin_allowed_ips=src.get("ADMIN_ALLOWED_IPS", ""),
        default_quota_mb=_int(src, "DEFAULT_QUOTA_MB", 2048),
        max_quota_mb=_int(src, "MAX_QUOTA_MB", 10240),
        invite_days=_int(src, "INVITE_DAYS", 7),
        min_username_len=_int(src, "MIN_USERNAME_LEN", 3),
        min_password_len=_int(src, "MIN_PASSWORD_LEN", 12),
        reserved_usernames=DEFAULT_RESERVED | extra_reserved,
        rl_register_per_hour=_int(src, "RL_REGISTER_PER_HOUR", 10),
        rl_invite_check_per_hour=_int(src, "RL_INVITE_CHECK_PER_HOUR", 30),
        rl_login_per_15min=_int(src, "RL_LOGIN_PER_15MIN", 5),
        rl_login_global_per_hour=_int(src, "RL_LOGIN_GLOBAL_PER_HOUR", 30),
        event_retention_days=_int(src, "EVENT_RETENTION_DAYS", 180),
    )
