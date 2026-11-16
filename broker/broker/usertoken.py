"""Verify an end user's Keycloak access token (PR 14).

The portal passes the signed-in user's access token with each call; the
control plane takes the user's identity (`sub`) only from a token whose
signature, issuer, expiry and authorised party (`azp`) check out, never from
a request field (contracts/capabilities.yaml, `subject_from`).

Keys come from the realm's JWKS endpoint (fetched directly on avedeus-edge),
cached, and refetched at most once a minute when a token names an unknown
key (Keycloak key rotation).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

import httpx
import jwt

ALGORITHMS = ["RS256", "PS256", "ES256"]
REFETCH_SECONDS = 60


class TokenError(Exception):
    pass


@dataclass
class UserTokenVerifier:
    jwks_url: str
    issuer: str
    authorized_party: str           # the client the token was issued to, e.g. avedeus-portal
    required_group: str | None = None  # e.g. "admins" for the admin console (PR 15)
    transport: httpx.BaseTransport | None = None
    clock: object = time.time
    leeway: int = 30
    _keys: dict = field(default_factory=dict, repr=False)
    _fetched: float = field(default=0.0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _fetch(self) -> None:
        try:
            with httpx.Client(timeout=httpx.Timeout(5.0), transport=self.transport) as c:
                r = c.get(self.jwks_url)
            r.raise_for_status()
            keys = {}
            for k in r.json().get("keys", []):
                if k.get("use", "sig") == "sig" and k.get("kid"):
                    keys[k["kid"]] = jwt.PyJWK.from_json(json.dumps(k))
        except (httpx.HTTPError, ValueError, jwt.PyJWKError) as e:
            raise TokenError(f"cannot load the identity provider's keys: {type(e).__name__}") from e
        self._keys, self._fetched = keys, self.clock()

    def _key(self, kid: str):
        with self._lock:
            if kid not in self._keys and self.clock() - self._fetched >= REFETCH_SECONDS or not self._keys:
                self._fetch()
            key = self._keys.get(kid)
        if key is None:
            raise TokenError("token signed with an unknown key")
        return key

    def verify(self, token: str) -> dict:
        """Claims of a valid token, or TokenError. Never trusts an unverified claim."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise TokenError("not a token") from e
        if header.get("alg") not in ALGORITHMS:
            raise TokenError("unexpected signing algorithm")
        key = self._key(str(header.get("kid", "")))
        try:
            claims = jwt.decode(token, key=key, algorithms=ALGORITHMS, issuer=self.issuer,
                                options={"verify_aud": False, "require": ["exp", "iat", "sub", "iss"]},
                                leeway=self.leeway)
        except jwt.PyJWTError as e:
            raise TokenError(f"invalid token: {type(e).__name__}") from e
        if claims.get("azp") != self.authorized_party:
            raise TokenError(f"token was not issued to {self.authorized_party}")
        if claims.get("typ", "Bearer") != "Bearer":
            raise TokenError("not an access token")
        if self.required_group:
            groups = {str(g).lstrip("/") for g in claims.get("groups") or []}
            if self.required_group not in groups:
                raise TokenError(f"not in the {self.required_group} group")
        return claims
