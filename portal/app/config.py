"""Portal settings, read once from environment variables (see portal.env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    public_url: str             # https://<mail host>
    base_path: str              # /account
    oidc_issuer: str            # https://<mail host>/auth/realms/avedeus
    keycloak_internal_url: str  # http://keycloak:8080/auth
    client_id: str
    client_secret: str
    control_plane_url: str
    control_plane_token: str
    session_secret: str
    secure_cookies: bool
    brand_name: str

    @property
    def redirect_uri(self) -> str:
        return f"{self.public_url}{self.base_path}/callback"

    def links(self) -> dict[str, str]:
        """Where native features live. The portal links; it does not rebuild."""
        u, kc = self.public_url, self.oidc_issuer
        return {
            "webmail": f"{u}/SOGo/",
            "mailcow_user": f"{u}/user",
            "kc_account": f"{kc}/account/",
            # Keycloak 26's account console (v3) uses path routes, not the old #/ ones (PR 17).
            "kc_password": f"{kc}/account/account-security/signing-in",
            "kc_sessions": f"{kc}/account/account-security/device-activity",
        }


def load_settings(env: dict[str, str] | None = None) -> Settings:
    src = env if env is not None else os.environ

    def req(name: str) -> str:
        v = src.get(name, "").strip()
        if not v:
            raise RuntimeError(f"Missing required setting {name}")
        return v

    public = req("PUBLIC_URL").rstrip("/")
    issuer = req("OIDC_ISSUER").rstrip("/")
    secure = src.get("SECURE_COOKIES", "true").strip().lower() in {"1", "true", "yes", "on"}
    if secure and not (public.startswith("https://") and issuer.startswith("https://")):
        raise RuntimeError("PUBLIC_URL and OIDC_ISSUER must be https:// (or set SECURE_COOKIES=false for tests)")
    secret = req("SESSION_SECRET")
    if len(secret) < 32:
        raise RuntimeError("SESSION_SECRET must be at least 32 characters")
    token = req("PORTAL_TOKEN")
    if len(token) < 32:
        raise RuntimeError("PORTAL_TOKEN must be at least 32 characters")
    base = "/" + src.get("BASE_PATH", "/account").strip().strip("/")
    return Settings(
        public_url=public,
        base_path=base if base != "/" else "",
        oidc_issuer=issuer,
        keycloak_internal_url=(src.get("KEYCLOAK_INTERNAL_URL", "").strip() or "http://keycloak:8080/auth").rstrip("/"),
        client_id=src.get("OIDC_CLIENT_ID", "").strip() or "avedeus-portal",
        client_secret=req("OIDC_CLIENT_SECRET"),
        control_plane_url=(src.get("CONTROL_PLANE_URL", "").strip() or "http://broker:8100").rstrip("/"),
        control_plane_token=token,
        session_secret=secret,
        secure_cookies=secure,
        brand_name=src.get("BRAND_NAME", "").strip() or "Avedeus Mail",
    )
