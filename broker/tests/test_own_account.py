"""PR 14: GET /v1/me, the portal's view of the signed-in user's own account.

The user comes only from a verified Keycloak access token. Fakes only.
"""

import json
import time

import httpx
import jwt
import pytest
from conftest import AUTH, Clock
from cryptography.hazmat.primitives.asymmetric import rsa
from fake_keycloak import CLIENT_SECRET, FakeKeycloak
from fastapi.testclient import TestClient

from broker.config import load_settings
from broker.keycloak import KeycloakClient
from broker.mailcow import MailcowClient
from broker.main import create_app
from broker.store import Store
from broker.usertoken import UserTokenVerifier
from fake_mailcow import FakeMailcow

ISSUER = "https://mx.example.com/auth/realms/avedeus"
PORTAL = {"Authorization": "Bearer " + "p" * 40}
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwks(key, kid="k1"):
    j = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {"keys": [{**j, "kid": kid, "use": "sig", "alg": "RS256"}]}


def token(sub="user-1", key=KEY, kid="k1", **over):
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": sub, "azp": "avedeus-portal", "typ": "Bearer", "iat": now, "exp": now + 300}
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def pctx(env):
    class C:
        pass
    c = C()
    env = {**env, "IDENTITY_PROVIDER": "keycloak", "KEYCLOAK_CLIENT_SECRET": CLIENT_SECRET,
           "PORTAL_TOKEN": "p" * 40, "PORTAL_CLIENTS": "*", "KEYCLOAK_ISSUER": ISSUER}
    c.fake, c.kc_fake, c.clock = FakeMailcow(), FakeKeycloak(), Clock()
    c.jwks_calls = 0

    def certs(request):
        c.jwks_calls += 1
        return httpx.Response(200, json=jwks(KEY))
    s = load_settings(env)
    c.verifier = UserTokenVerifier("http://keycloak:8080/auth/realms/avedeus/protocol/openid-connect/certs",
                                   ISSUER, "avedeus-portal", transport=httpx.MockTransport(certs), clock=c.clock)
    c.store = Store(":memory:", clock=c.clock)
    mc = MailcowClient(s.mailcow_api_url, s.mailcow_api_key, transport=c.fake.transport())
    kc = KeycloakClient(s.keycloak_url, s.keycloak_realm, s.keycloak_client_id, s.keycloak_client_secret,
                        transport=c.kc_fake.transport())
    c.client = TestClient(create_app(s, mailcow=mc, keycloak=kc, store=c.store, user_tokens=c.verifier, clock=c.clock))
    c.fake.mailboxes["alice@example.com"] = {"username": "alice@example.com", "name": "Alice", "active": 1,
                                             "quota": 2048 * 1048576, "quota_used": 300 * 1048576}
    c.store.link_set("alice@example.com", "keycloak", "user-1")
    return c


def me(c, tok, headers=PORTAL):
    return c.client.get("/v1/me", headers={**headers, "X-User-Token": tok})


def test_own_account_from_a_verified_token(pctx):
    r = me(pctx, token())
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "address": "alice@example.com", "name": "Alice",
                        "quota_mb": 2048, "used_mb": 300, "active": True}


@pytest.mark.parametrize("bad", [
    lambda: token(key=OTHER_KEY),                        # forged signature
    lambda: token(iss="https://evil.test/auth/realms/avedeus"),
    lambda: token(azp="mailcow"),                        # issued to another client
    lambda: token(exp=int(time.time()) - 3600),          # expired
    lambda: token(typ="ID"),                             # an ID token, not an access token
    lambda: "not-a-jwt",
    lambda: jwt.encode({"iss": ISSUER, "sub": "user-1", "azp": "avedeus-portal", "iat": 1, "exp": 9999999999},
                       "secret", algorithm="HS256", headers={"kid": "k1"}),   # alg confusion
])
def test_bad_user_tokens_are_refused_and_audited(pctx, bad):
    r = me(pctx, bad())
    assert r.status_code == 401 and r.json()["kind"] == "unauthorized"
    assert pctx.store.events()[0]["action"] == "auth.refused"


def test_subject_never_comes_from_the_request(pctx):
    r = pctx.client.get("/v1/me?address=bob@example.com", headers={**PORTAL, "X-User-Token": token()})
    assert r.json()["address"] == "alice@example.com"


def test_unlinked_identity_is_not_found(pctx):
    r = me(pctx, token(sub="someone-else"))
    assert r.status_code == 404 and r.json()["kind"] == "not_found"


def test_only_the_portal_caller_may_ask(pctx):
    assert me(pctx, token(), headers=AUTH).status_code == 403
    assert pctx.client.get("/v1/me", headers={"X-User-Token": token()}).status_code == 401


def test_keys_are_cached_and_refetched_for_a_new_kid(pctx):
    me(pctx, token())
    me(pctx, token())
    assert pctx.jwks_calls == 1
    pctx.clock.t += 120
    assert me(pctx, token(kid="rotated")).status_code == 401   # unknown kid: one refetch, still unknown
    assert pctx.jwks_calls == 2
    assert me(pctx, token(kid="rotated")).status_code == 401   # no refetch storm
    assert pctx.jwks_calls == 2


def test_portal_needs_keycloak_and_issuer(env):
    with pytest.raises(RuntimeError, match="KEYCLOAK_ISSUER"):
        load_settings({**env, "PORTAL_TOKEN": "p" * 40, "PORTAL_CLIENTS": "*"})
