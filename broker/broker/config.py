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
OPTIONAL_CALLERS = ("monitoring", "admin", "portal")


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
    # PR 12: "none" (mailcow logins, as before) or "keycloak" (one identity).
    identity_provider: str = "none"
    keycloak_url: str = "http://keycloak:8080/auth"
    keycloak_realm: str = "avedeus"
    keycloak_client_id: str = "avedeus-control-plane"
    keycloak_client_secret: str = ""
    keycloak_group: str = "users"
    mailbox_template: str = ""   # value of the mailcow_template user attribute, if set
    # PR 34: the message trace reads mailcow with its own read-only key.
    mailcow_read_only_api_key: str = ""
    trace_log_lines: int = 10000
    # PR 14: the portal's users. Their Keycloak access tokens must come from this
    # issuer (the public URL) and be issued to this client.
    keycloak_issuer: str = ""
    portal_client_id: str = "avedeus-portal"
    admin_client_id: str = "avedeus-admin"
    admin_group: str = "admins"
    # PR 15: admin tokens must show a second factor (amr contains "otp").
    admin_require_otp: bool = True
    # PR 16 (ADR 0021, roadmap D6): days between scheduling a deletion and the
    # deletion job removing the account (and only after a newer backup).
    deletion_grace_days: int = 30


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

    idp = (src.get("IDENTITY_PROVIDER", "").strip().lower() or "none")
    if idp not in ("none", "keycloak"):
        raise RuntimeError("IDENTITY_PROVIDER must be none or keycloak")
    kc_secret = src.get("KEYCLOAK_CLIENT_SECRET", "").strip()
    if idp == "keycloak" and len(kc_secret) < 32:
        raise RuntimeError("IDENTITY_PROVIDER=keycloak needs KEYCLOAK_CLIENT_SECRET (at least 32 characters)")

    ro_key = src.get("MAILCOW_READ_ONLY_API_KEY", "").strip()
    if ro_key and ro_key == src.get("MAILCOW_API_KEY", "").strip():
        raise RuntimeError("MAILCOW_READ_ONLY_API_KEY must be mailcow's read-only key, not the read-write one")

    issuer = src.get("KEYCLOAK_ISSUER", "").strip().rstrip("/")
    if src.get("PORTAL_TOKEN", "").strip() and (idp != "keycloak" or not issuer.startswith("https://")):
        raise RuntimeError("the portal caller needs IDENTITY_PROVIDER=keycloak and KEYCLOAK_ISSUER "
                           "(https://<mail host>/auth/realms/avedeus)")

    grace = num("DELETION_GRACE_DAYS", 30)
    if not 1 <= grace <= 365:
        raise RuntimeError("DELETION_GRACE_DAYS must be 1-365")

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
        identity_provider=idp,
        keycloak_url=(src.get("KEYCLOAK_URL", "").strip() or "http://keycloak:8080/auth").rstrip("/"),
        keycloak_realm=src.get("KEYCLOAK_REALM", "").strip() or "avedeus",
        keycloak_client_id=src.get("KEYCLOAK_CLIENT_ID", "").strip() or "avedeus-control-plane",
        keycloak_client_secret=kc_secret,
        keycloak_group=src.get("KEYCLOAK_GROUP", "").strip() or "users",
        mailbox_template=src.get("MAILBOX_TEMPLATE", "").strip(),
        mailcow_read_only_api_key=ro_key,
        trace_log_lines=num("TRACE_LOG_LINES", 10000),
        keycloak_issuer=issuer,
        portal_client_id=src.get("PORTAL_CLIENT_ID", "").strip() or "avedeus-portal",
        admin_client_id=src.get("ADMIN_CLIENT_ID", "").strip() or "avedeus-admin",
        admin_group=src.get("ADMIN_GROUP", "").strip() or "admins",
        admin_require_otp=src.get("ADMIN_REQUIRE_OTP", "true").strip().lower() in {"1", "true", "yes", "on"},
        deletion_grace_days=grace,
    )
