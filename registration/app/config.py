"""Settings, read once from environment variables (see .env.example)."""

from __future__ import annotations

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
    # provisioning broker (the app never holds the mailcow API key)
    broker_url: str
    broker_token: str
    mail_domain: str
    webmail_url: str
    public_url: str  # where this app is reached, e.g. https://mail.example.com (base_path is appended)

    brand_name: str

    # app
    base_path: str
    database_path: str
    session_secret: str
    secure_cookies: bool

    # PR 15 (ADR 0020): the admin console's call to this app's internal invitation API.
    # Empty token = the internal API is off (invitations then come from the CLI only).
    admin_console_token: str
    admin_console_clients: str  # comma-separated IPs/CIDRs; default: the admin console's fixed address

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

    # event log rows (which hold client IPs) older than this are deleted; 0 = keep forever
    event_retention_days: int = 180


# PR 15: the admin pages moved to the admin console (admin/, ADR 0020).
REMOVED = ("ADMIN_USERNAME", "ADMIN_PASSWORD_HASH", "ADMIN_TOTP_SECRET", "ADMIN_ALLOWED_IPS", "ADMIN_ALLOW_ANY",
           "RL_LOGIN_PER_15MIN", "RL_LOGIN_GLOBAL_PER_HOUR")


def _console(src) -> tuple[str, str]:
    token = src.get("ADMIN_CONSOLE_TOKEN", "").strip()
    if token and len(token) < 32:
        raise RuntimeError("ADMIN_CONSOLE_TOKEN must be at least 32 characters (openssl rand -hex 32)")
    clients = src.get("ADMIN_CONSOLE_CLIENTS", "").strip() or "172.30.240.14"
    from .security import parse_networks
    try:
        parse_networks(clients)
    except ValueError as e:
        raise RuntimeError(f"ADMIN_CONSOLE_CLIENTS is not a valid list of IPs/CIDRs: {e}") from None
    return token, clients


def _broker_token(src) -> str:
    if src.get("MAILCOW_API_KEY", "").strip():
        # Guard against an old .env: the key belongs in broker.env only.
        raise RuntimeError("MAILCOW_API_KEY must not be set for the registration app; "
                           "it belongs in broker.env (the provisioning broker)")
    token = src.get("BROKER_TOKEN", "").strip()
    if len(token) < 32:
        raise RuntimeError("BROKER_TOKEN must be set (at least 32 characters, same as SIGNUP_TOKEN in broker.env)")
    return token


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

    stale = [k for k in REMOVED if src.get(k, "").strip()]
    if stale:
        raise RuntimeError(f"{', '.join(stale)}: the admin pages left the sign-up app in PR 15. Remove these "
                           "from .env; invitations are managed in the admin console or with python -m app.cli "
                           "(docs/admin-console.md)")

    secret = req("SESSION_SECRET")
    if len(secret) < 32:
        raise RuntimeError("SESSION_SECRET must be at least 32 characters")

    console_token, console_clients = _console(src)
    if console_token and console_token == src.get("BROKER_TOKEN", "").strip():
        raise RuntimeError("ADMIN_CONSOLE_TOKEN must differ from BROKER_TOKEN (one credential per caller)")
    return Settings(
        broker_url=src.get("BROKER_URL", "http://broker:8100").rstrip("/"),
        broker_token=_broker_token(src),
        mail_domain=domain,
        webmail_url=src.get("WEBMAIL_URL", f"https://mail.{domain}").rstrip("/"),
        public_url=src.get("PUBLIC_URL", src.get("WEBMAIL_URL", f"https://mail.{domain}")).rstrip("/"),
        brand_name=src.get("BRAND_NAME", "Avedeus Mail"),
        base_path=base_path,
        database_path=src.get("DATABASE_PATH", "/data/registration.sqlite3"),
        session_secret=secret,
        secure_cookies=_bool(src, "SECURE_COOKIES", True),
        admin_console_token=console_token,
        admin_console_clients=console_clients,
        default_quota_mb=_int(src, "DEFAULT_QUOTA_MB", 2048),
        max_quota_mb=_int(src, "MAX_QUOTA_MB", 10240),
        invite_days=_int(src, "INVITE_DAYS", 7),
        min_username_len=_int(src, "MIN_USERNAME_LEN", 3),
        min_password_len=_int(src, "MIN_PASSWORD_LEN", 12),
        reserved_usernames=DEFAULT_RESERVED | extra_reserved,
        rl_register_per_hour=_int(src, "RL_REGISTER_PER_HOUR", 10),
        rl_invite_check_per_hour=_int(src, "RL_INVITE_CHECK_PER_HOUR", 30),
        event_retention_days=_int(src, "EVENT_RETENTION_DAYS", 180),
    )
