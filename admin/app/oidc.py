"""The admin console's OpenID Connect client (copied from portal/app/oidc.py; keep the two in step): code exchange, refresh, ID-token claims.

The browser goes to Keycloak's public URL; the console talks to Keycloak's
token endpoint directly on avedeus-edge (back channel). Because the ID token
arrives straight from the token endpoint over that direct connection, its
issuer is checked from the connection rather than the signature (OpenID
Connect Core 3.1.3.7, step 6); its claims (issuer, audience, expiry, nonce)
are still checked. The control plane, the security boundary, verifies the
access token's signature on every call.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

import httpx


class OidcError(Exception):
    pass


@dataclass
class OidcClient:
    issuer: str                 # public, e.g. https://mx.example.com/auth/realms/avedeus
    internal_url: str           # e.g. http://keycloak:8080/auth (back channel)
    client_id: str
    client_secret: str
    transport: httpx.BaseTransport | None = None
    clock: object = time.time

    @property
    def token_url(self) -> str:
        realm = self.issuer.rstrip("/").rsplit("/", 1)[1]
        return f"{self.internal_url.rstrip('/')}/realms/{realm}/protocol/openid-connect/token"

    def _token(self, form: dict) -> dict:
        try:
            with httpx.Client(timeout=httpx.Timeout(10.0), transport=self.transport) as c:
                r = c.post(self.token_url, data={**form, "client_id": self.client_id,
                                                 "client_secret": self.client_secret})
        except httpx.HTTPError as e:
            raise OidcError(f"token endpoint unreachable: {type(e).__name__}") from e
        try:
            data = r.json()
        except ValueError as e:
            raise OidcError(f"token endpoint HTTP {r.status_code}, not JSON") from e
        if r.status_code != 200 or "access_token" not in data:
            raise OidcError(f"token endpoint refused: {data.get('error', r.status_code)}")
        return data

    def exchange(self, code: str, verifier: str | None, redirect_uri: str) -> dict:
        if not code or not verifier:
            raise OidcError("missing code or PKCE verifier")
        t = self._token({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                         "code_verifier": verifier})
        if "id_token" not in t:
            raise OidcError("no ID token (is the openid scope allowed?)")
        return t

    def refresh(self, refresh_token: str) -> dict:
        if not refresh_token:
            raise OidcError("no refresh token")
        return self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def id_claims(self, id_token: str, nonce: str | None) -> dict:
        try:
            payload = id_token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (IndexError, ValueError) as e:
            raise OidcError("malformed ID token") from e
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if claims.get("iss") != self.issuer:
            raise OidcError("ID token from another issuer")
        if self.client_id not in auds:
            raise OidcError("ID token for another client")
        if not nonce or claims.get("nonce") != nonce:
            raise OidcError("ID token nonce mismatch")
        if float(claims.get("exp", 0)) < self.clock() - 30:
            raise OidcError("ID token expired")
        if not claims.get("sub"):
            raise OidcError("ID token without subject")
        return claims
