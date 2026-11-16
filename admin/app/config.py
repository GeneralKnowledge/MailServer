"""Admin console settings, read once from environment variables (see admin.env.example)."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    public_url: str             # https://<mail host>
    base_path: str              # /console (mailcow already owns /admin)
    oidc_issuer: str            # https://<mail host>/auth/realms/avedeus
    keycloak_internal_url: str  # http://keycloak:8080/auth
    client_id: str              # avedeus-admin
    client_secret: str
    control_plane_url: str
    control_plane_token: str    # the control plane's ADMIN_TOKEN
    signup_url: str             # the sign-up app's internal API (ADR 0020)
    signup_token: str           # the sign-up app's ADMIN_CONSOLE_TOKEN
    session_secret: str
    secure_cookies: bool
    brand_name: str
    admin_group: str
    require_otp: bool
    allowed_ips: tuple          # networks allowed to reach the console; empty only with ADMIN_ALLOW_ANY=1

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url}{self.base_path}/callback"

    def links(self) -> dict[str, str]:
        """Where native admin features live. The console links; it does not rebuild."""
        u, kc = self.public_url, self.oidc_issuer
        realm_admin = kc.replace("/realms/", "/admin/master/console/#/")
        return {
            "mailcow_admin": f"{u}/admin",
            "keycloak_users": f"{realm_admin}/users",
            "keycloak_events": f"{realm_admin}/events",
            "kc_account": f"{kc}/account/",
        }


def _bool(src, name: str, default: bool) -> bool:
    v = src.get(name)
    return default if v is None or not v.strip() else v.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: dict[str, str] | None = None) -> Settings:
    src = env if env is not None else os.environ

    def req(name: str, min_len: int = 1) -> str:
        v = src.get(name, "").strip()
        if not v:
            raise RuntimeError(f"Missing required setting {name}")
        if len(v) < min_len:
            raise RuntimeError(f"{name} must be at least {min_len} characters")
        return v

    public = req("PUBLIC_URL").rstrip("/")
    issuer = req("OIDC_ISSUER").rstrip("/")
    secure = _bool(src, "SECURE_COOKIES", True)
    if secure and not (public.startswith("https://") and issuer.startswith("https://")):
        raise RuntimeError("PUBLIC_URL and OIDC_ISSUER must be https:// (or set SECURE_COOKIES=false for tests)")
    cp_token, signup_token = req("CONTROL_PLANE_TOKEN", 32), req("SIGNUP_CONSOLE_TOKEN", 32)
    if cp_token == signup_token:
        raise RuntimeError("CONTROL_PLANE_TOKEN and SIGNUP_CONSOLE_TOKEN must differ (one credential per caller)")
    ips = src.get("ADMIN_ALLOWED_IPS", "").strip()
    if not ips and not _bool(src, "ADMIN_ALLOW_ANY", False):
        raise RuntimeError("Set ADMIN_ALLOWED_IPS (your IP or VPN range) or, to accept the risk, ADMIN_ALLOW_ANY=1")
    try:
        nets = tuple(ipaddress.ip_network(p.strip(), strict=False) for p in ips.split(",") if p.strip())
    except ValueError as e:
        raise RuntimeError(f"ADMIN_ALLOWED_IPS is not a valid list of IPs/CIDRs: {e}") from None
    base = "/" + src.get("BASE_PATH", "/console").strip().strip("/")
    return Settings(
        public_url=public,
        base_path=base if base != "/" else "",
        oidc_issuer=issuer,
        keycloak_internal_url=(src.get("KEYCLOAK_INTERNAL_URL", "").strip() or "http://keycloak:8080/auth").rstrip("/"),
        client_id=src.get("OIDC_CLIENT_ID", "").strip() or "avedeus-admin",
        client_secret=req("OIDC_CLIENT_SECRET"),
        control_plane_url=(src.get("CONTROL_PLANE_URL", "").strip() or "http://broker:8100").rstrip("/"),
        control_plane_token=cp_token,
        signup_url=(src.get("SIGNUP_URL", "").strip() or "http://registration:8000").rstrip("/"),
        signup_token=signup_token,
        session_secret=req("SESSION_SECRET", 32),
        secure_cookies=secure,
        brand_name=src.get("BRAND_NAME", "").strip() or "Avedeus Mail",
        admin_group=src.get("ADMIN_GROUP", "").strip() or "admins",
        require_otp=_bool(src, "REQUIRE_OTP", True),
        allowed_ips=nets,
    )
