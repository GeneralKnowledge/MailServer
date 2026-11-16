"""Keep the architecture contracts consistent with each other and with the code.

- control-plane.openapi.yaml is well formed and every operation says whether
  it is implemented or planned (and for which PR);
- capabilities.yaml grants only operations that exist, grants every
  operation to someone, and never gives the public sign-up caller lifecycle,
  alias or audit operations (ADR 0010);
- the broker's real routes are exactly the contract's implemented operations,
  and its real responses match the contract's schemas;
- the enforced capability rows match the broker's own copy, and the running
  broker answers 401/403/allowed exactly as they say (PR 9);
- the requests the sign-up app really sends match the request schemas, and
  the broker refuses bodies the schemas refuse (PR 9);
- every audit event the broker really writes matches the audit schema;
- audit-event.schema.json is a valid schema that refuses secrets.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"
HTTP_METHODS = {"get", "put", "post", "patch", "delete"}


@pytest.fixture(scope="module")
def spec():
    return yaml.safe_load((CONTRACTS / "control-plane.openapi.yaml").read_text())


@pytest.fixture(scope="module")
def caps():
    return yaml.safe_load((CONTRACTS / "capabilities.yaml").read_text())


@pytest.fixture(scope="module")
def audit_schema():
    return json.loads((CONTRACTS / "audit-event.schema.json").read_text())


def operations(spec):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method in HTTP_METHODS:
                yield method.upper(), path, op


def by_id(spec):
    return {op["operationId"]: (m, p, op) for m, p, op in operations(spec)}


def resolve(node, spec):
    """Inline local $refs so a schema can be checked with plain jsonschema."""
    if isinstance(node, dict):
        if "$ref" in node:
            target = spec
            for part in node["$ref"].lstrip("#/").split("/"):
                target = target[part]
            return resolve(copy.deepcopy(target), spec)
        return {k: resolve(v, spec) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(v, spec) for v in node]
    return node


# --- the contract itself ----------------------------------------------------

def test_every_operation_is_labelled(spec):
    ids = [op.get("operationId") for _, _, op in operations(spec)]
    assert all(ids), "every operation needs an operationId"
    assert len(ids) == len(set(ids)), "operationIds must be unique"
    for method, path, op in operations(spec):
        status = op.get("x-avedeus-status")
        assert status in {"implemented", "planned"}, f"{method} {path}: x-avedeus-status"
        if status == "planned":
            assert isinstance(op.get("x-avedeus-pr"), int), f"{method} {path}: planned needs x-avedeus-pr"


def test_refs_resolve_and_schemas_are_valid(spec):
    for method, path, op in operations(spec):
        for code, resp in resolve(op.get("responses", {}), spec).items():
            for media in resp.get("content", {}).values():
                jsonschema.Draft202012Validator.check_schema(media["schema"])
        body = resolve(op.get("requestBody", {}), spec)
        for media in body.get("content", {}).values():
            jsonschema.Draft202012Validator.check_schema(media["schema"])


def test_planned_state_changes_take_an_idempotency_key(spec):
    for method, path, op in operations(spec):
        if op["x-avedeus-status"] != "planned" or method == "GET":
            continue
        if "password-manager" in op.get("tags", []):
            continue  # shape fixed by the SimpleLogin/addy clients we copy
        params = [p.get("$ref", "") for p in op.get("parameters", [])]
        assert "#/components/parameters/IdempotencyKey" in params, f"{op['operationId']} needs Idempotency-Key"


def test_state_changes_name_an_audit_action(spec):
    for method, path, op in operations(spec):
        if method != "GET" and op["operationId"] != "checkAddress":
            assert op.get("x-avedeus-audit"), f"{op['operationId']} must name its audit action"


# --- capabilities -----------------------------------------------------------

def test_capabilities_name_real_operations(spec, caps):
    ids = set(by_id(spec))
    granted = set(caps["public"])
    for name, row in caps["callers"].items():
        unknown = set(row["operations"]) - ids
        assert not unknown, f"{name} grants unknown operations {unknown}"
        granted |= set(row["operations"])
    assert ids - granted == set(), f"operations granted to nobody: {ids - granted}"


def test_signup_is_never_granted_lifecycle_alias_or_audit(spec, caps):
    ops = by_id(spec)
    for op_id in caps["callers"]["signup"]["operations"]:
        tags = set(ops[op_id][2].get("tags", []))
        assert tags <= {"signup"}, f"signup must not get {op_id} ({tags})"


def test_only_admin_gets_lifecycle_and_audit(spec, caps):
    ops = by_id(spec)
    lifecycle = {i for i, (_, _, op) in ops.items() if "lifecycle" in op.get("tags", [])}
    assert set(caps["lifecycle_operations"]) <= lifecycle
    for name, row in caps["callers"].items():
        if name == "admin":
            continue
        for op_id in row["operations"]:
            tags = set(ops[op_id][2].get("tags", []))
            assert not tags & {"lifecycle", "audit"}, f"{name} must not get {op_id}"


def test_only_admin_gets_diagnostics(spec, caps):
    ops = by_id(spec)
    for name, row in caps["callers"].items():
        for op_id in row["operations"]:
            if "diagnostics" in ops[op_id][2].get("tags", []):
                assert name == "admin", f"{name} must not get {op_id} (PR 34: admin only)"


def test_monitoring_gets_health_only(spec, caps):
    ops = by_id(spec)
    for op_id in caps["callers"]["monitoring"]["operations"]:
        assert ops[op_id][2].get("tags") == ["health"], op_id


def test_every_row_says_whether_it_is_enforced(caps):
    # No caller separation exists before PR 9; the table must not claim otherwise.
    for name, row in caps["callers"].items():
        assert isinstance(row.get("enforced"), bool), f"{name}: enforced must be true or false"


def test_tokens_cannot_exceed_portal(caps):
    portal = set(caps["callers"]["portal"]["operations"])
    assert set(caps["callers"]["personal-token"]["operations"]) <= portal


# --- the broker matches the contract ----------------------------------------

SIGNUP = {"Authorization": "Bearer " + "s" * 40}
MONITORING = {"Authorization": "Bearer " + "m" * 40}
ADMIN = {"Authorization": "Bearer " + "a" * 40}
CALLER_AUTH = {"signup": SIGNUP, "monitoring": MONITORING, "admin": ADMIN}


def _broker_modules():
    sys.path.insert(0, str(ROOT / "broker"))
    sys.path.insert(0, str(ROOT / "broker" / "tests"))
    from broker import capabilities, config, mailcow, main, store
    import fake_mailcow
    return capabilities, config, mailcow, main, store, fake_mailcow


@pytest.fixture
def broker():
    _, config, mailcow, main, store, fake_mailcow = _broker_modules()
    from fastapi.testclient import TestClient

    settings = config.load_settings({
        "MAILCOW_API_URL": "https://mail.example.com", "MAILCOW_API_KEY": fake_mailcow.API_KEY,
        "MAIL_DOMAIN": "example.com", "SIGNUP_TOKEN": "s" * 40, "SIGNUP_CLIENTS": "*",
        "MONITORING_TOKEN": "m" * 40, "MONITORING_CLIENTS": "*", "MAX_CREATES_PER_DAY": "1",
        "ADMIN_TOKEN": "a" * 40, "ADMIN_CLIENTS": "*", "MAILCOW_READ_ONLY_API_KEY": fake_mailcow.RO_API_KEY,
    })
    fake = fake_mailcow.FakeMailcow()
    mc = mailcow.MailcowClient(settings.mailcow_api_url, settings.mailcow_api_key, transport=fake.transport())
    db = store.Store(":memory:")
    app = main.create_app(settings, mailcow=mc, store=db)
    return app, TestClient(app), db


def test_broker_routes_equal_implemented_operations(spec, broker):
    app = broker[0]
    routes = {(m, r.path) for r in app.routes if getattr(r, "methods", None)
              for m in r.methods if m.lower() in HTTP_METHODS}
    implemented = {(m, p) for m, p, op in operations(spec) if op["x-avedeus-status"] == "implemented"}
    assert routes == implemented, (
        f"in broker but not in contract: {routes - implemented}; "
        f"in contract but not in broker: {implemented - routes}"
    )
    ops = {r.operation_id for r in app.routes if getattr(r, "methods", None)}
    assert ops == {i for i, (_, _, op) in by_id(spec).items() if op["x-avedeus-status"] == "implemented"}


def _check(spec, op_id, response):
    _, _, op = by_id(spec)[op_id]
    declared = op["responses"].get(str(response.status_code))
    assert declared, f"{op_id}: status {response.status_code} not in contract"
    schema = resolve(declared, spec)["content"]["application/json"]["schema"]
    jsonschema.validate(response.json(), schema)


def test_broker_responses_match_contract(spec, broker):
    _, client, _ = broker
    _check(spec, "healthz", client.get("/healthz"))
    _check(spec, "health", client.get("/v1/health", headers=MONITORING))
    _check(spec, "checkAddress", client.post("/v1/addresses/check", json={"local_part": "carol"}, headers=SIGNUP))
    _check(spec, "checkAddress", client.post("/v1/addresses/check", json={"local_part": "admin"}, headers=SIGNUP))
    _check(spec, "checkAddress", client.post("/v1/addresses/check", json={}, headers=SIGNUP))         # 422
    body = {"local_part": "carol", "password": "Sunny-River-42x", "name": "C", "quota_mb": 1024}
    key = {"Idempotency-Key": "k" * 20}
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json=body, headers={**SIGNUP, **key}))  # 201
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json=body, headers={**SIGNUP, **key}))  # replay 201
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json={**body, "local_part": "dave"},
                                              headers={**SIGNUP, **key}))                                # 409 conflict
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json={**body, "local_part": "erin"},
                                              headers=SIGNUP))                                           # 429 cap
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json={**body, "quota_mb": 0}, headers=SIGNUP))  # 422
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json={**body, "password": "x"}, headers=SIGNUP))  # 422
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json=body))                               # 401
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json=body, headers=MONITORING))           # 403
    _check(spec, "upstreamCheck", client.get("/v1/upstream", headers=MONITORING))
    _check(spec, "traceMessage", client.get("/v1/trace", params={"address": "carol@example.com"}, headers=ADMIN))
    _check(spec, "traceMessage", client.get("/v1/trace", params={"hours": 0}, headers=ADMIN))           # 422
    _check(spec, "traceMessage", client.get("/v1/trace", headers=SIGNUP))                               # 403


def _broker_with_keycloak():
    _, config, mailcow, main, store, fake_mailcow = _broker_modules()
    import fake_keycloak
    from broker import keycloak
    from fastapi.testclient import TestClient

    settings = config.load_settings({
        "MAILCOW_API_URL": "https://mail.example.com", "MAILCOW_API_KEY": fake_mailcow.API_KEY,
        "MAIL_DOMAIN": "example.com", "SIGNUP_TOKEN": "s" * 40, "SIGNUP_CLIENTS": "*",
        "MONITORING_TOKEN": "m" * 40, "MONITORING_CLIENTS": "*", "MAX_CREATES_PER_DAY": "1",
        "IDENTITY_PROVIDER": "keycloak", "KEYCLOAK_CLIENT_SECRET": fake_keycloak.CLIENT_SECRET,
    })
    mc = mailcow.MailcowClient(settings.mailcow_api_url, settings.mailcow_api_key,
                               transport=fake_mailcow.FakeMailcow().transport())
    kc = keycloak.KeycloakClient(settings.keycloak_url, settings.keycloak_realm, settings.keycloak_client_id,
                                 settings.keycloak_client_secret, transport=fake_keycloak.FakeKeycloak().transport())
    db = store.Store(":memory:")
    return TestClient(main.create_app(settings, mailcow=mc, store=db, keycloak=kc)), db


ACCOUNT = {"local_part": "carol", "password": "Sunny-River-42x-long", "name": "C", "quota_mb": 1024,
           "invitation": "inv-1"}


@pytest.mark.parametrize("with_keycloak", [False, True])
def test_create_account_responses_match_contract(spec, broker, audit_schema, with_keycloak):
    client, db = _broker_with_keycloak() if with_keycloak else broker[1:]
    key = {"Idempotency-Key": "k" * 20}
    _check(spec, "createAccount", client.post("/v1/accounts", json=ACCOUNT, headers={**SIGNUP, **key}))  # 201
    _check(spec, "createAccount", client.post("/v1/accounts", json=ACCOUNT, headers={**SIGNUP, **key}))  # replay
    _check(spec, "createAccount", client.post("/v1/accounts", json=ACCOUNT, headers=SIGNUP))  # 422 no key
    _check(spec, "createAccount", client.post("/v1/accounts", json={**ACCOUNT, "local_part": "dave"},
                                              headers={**SIGNUP, "Idempotency-Key": "j" * 20}))  # 429 cap
    _check(spec, "createAccount", client.post("/v1/accounts", json=ACCOUNT, headers=MONITORING))  # 403
    if with_keycloak:
        _check(spec, "createMailbox", client.post("/v1/mailboxes", json={k: v for k, v in ACCOUNT.items()
                                                                          if k != "invitation"}, headers=SIGNUP))  # 409
        _check(spec, "health", client.get("/v1/health", headers=MONITORING))
    events = db.events()
    assert "account.create" in {e["action"] for e in events}
    for e in events:
        jsonschema.validate(e, audit_schema, format_checker=jsonschema.FormatChecker())


# --- PR 9: capabilities are enforced as written ------------------------------

def test_enforced_rows_match_the_brokers_copy(spec, caps):
    capabilities = _broker_modules()[0]
    implemented = {i for i, (_, _, op) in by_id(spec).items() if op["x-avedeus-status"] == "implemented"}
    enforced = {n: set(r["operations"]) & implemented for n, r in caps["callers"].items() if r["enforced"]}
    assert enforced == {n: set(v) for n, v in capabilities.CAPABILITIES.items()}
    assert set(caps["public"]) == set(capabilities.PUBLIC)
    # Nothing implemented is reachable by a caller whose row isn't enforced.
    for name, row in caps["callers"].items():
        if not row["enforced"]:
            assert name not in capabilities.CAPABILITIES


def _call(client, method, path, headers):
    body = {"local_part": "zed", "password": "Sunny-River-42x", "quota_mb": 10} if path == "/v1/mailboxes" \
        else {"local_part": "zed"} if method == "POST" else None
    return client.request(method, path, json=body, headers=headers)


def test_authentication_matches_the_security_declarations(spec, caps, broker):
    _, client, _ = broker
    for method, path, op in operations(spec):
        if op["x-avedeus-status"] != "implemented":
            continue
        op_id = op["operationId"]
        public = op.get("security") == []
        assert public == (op_id in caps["public"]), f"{op_id}: security vs capabilities.yaml public"
        anon = _call(client, method, path, {})
        if public:
            assert anon.status_code < 400, op_id
            continue
        assert anon.status_code == 401 and anon.json()["kind"] == "unauthorized", op_id
        wrong = _call(client, method, path, {"Authorization": "Bearer " + "w" * 40})
        assert wrong.status_code == 401, op_id
        for name, headers in CALLER_AUTH.items():
            r = _call(client, method, path, headers)
            if op_id in caps["callers"][name]["operations"]:
                assert r.status_code not in (401, 403), f"{name} should reach {op_id}: {r.status_code}"
            else:
                assert r.status_code == 403 and r.json()["kind"] == "forbidden", f"{name} must not reach {op_id}"


# --- PR 9: requests match the request schemas ----------------------------------

def _request_schema(spec, op_id):
    return resolve(by_id(spec)[op_id][2]["requestBody"], spec)["content"]["application/json"]["schema"]


def test_signup_app_requests_match_the_contract(spec):
    sys.path.insert(0, str(ROOT / "registration"))
    import httpx
    from app.provisioning import BrokerClient

    sent = []

    def handler(request):
        sent.append((request.url.path, json.loads(request.content), dict(request.headers)))
        if request.url.path == "/v1/mailboxes":
            return httpx.Response(201, json={"ok": True, "address": "alice@example.com"})
        return httpx.Response(200, json={"ok": True, "valid": True, "reason": None, "taken": False, "mailbox": False})

    c = BrokerClient("http://broker", "s" * 40, transport=httpx.MockTransport(handler))
    c.check("alice")
    c.create_mailbox(local_part="alice", password="Sunny-River-42x", quota_mb=1024, name="Alice",
                     idempotency_key="claim-" + "a" * 32)
    paths = {p: (m, op) for m, p, op in operations(spec)}
    for path, body, headers in sent:
        _, op = paths[path]
        jsonschema.validate(body, _request_schema(spec, op["operationId"]))
        assert headers["authorization"].startswith("Bearer ")
    create_headers = sent[-1][2]
    param = next(p for p in by_id(spec)["createMailbox"][2]["parameters"] if p["name"] == "Idempotency-Key")
    jsonschema.validate(create_headers["idempotency-key"], param["schema"])


@pytest.mark.parametrize("op_id,path,body", [
    ("createMailbox", "/v1/mailboxes", {"local_part": "x", "password": "Sunny-River-42x", "quota_mb": 1, "domain": "a"}),
    ("createMailbox", "/v1/mailboxes", {"local_part": "x", "password": "short", "quota_mb": 1}),
    ("createMailbox", "/v1/mailboxes", {"local_part": "x", "quota_mb": 1}),
    ("checkAddress", "/v1/addresses/check", {"local_part": "x", "extra": True}),
    ("checkAddress", "/v1/addresses/check", {"local_part": "x" * 65}),
])
def test_broker_refuses_what_the_request_schema_refuses(spec, broker, op_id, path, body):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(body, _request_schema(spec, op_id))
    r = broker[1].post(path, json=body, headers=SIGNUP)
    assert r.status_code == 422 and r.json()["kind"] == "invalid"
    assert body.get("password", "\0") not in r.text


def test_events_the_broker_writes_match_the_audit_schema(broker, audit_schema):
    _, client, db = broker
    body = {"local_part": "carol", "password": "Sunny-River-42x", "quota_mb": 1024}
    client.post("/v1/mailboxes", json=body, headers={**SIGNUP, "Idempotency-Key": "k" * 20})
    client.post("/v1/mailboxes", json=body, headers=SIGNUP)                      # refused: limit
    client.post("/v1/mailboxes", json={"x": 1}, headers=SIGNUP)                  # request.invalid
    client.post("/v1/mailboxes", json=body)                                      # auth.refused unknown
    client.get("/v1/upstream", headers=SIGNUP)                                   # auth.refused forbidden
    client.get("/v1/trace", params={"address": "carol@example.com"}, headers=ADMIN)  # message.trace
    reconcile = __import__("broker.reconcile", fromlist=["run"])
    _, config, mailcow, _, _, fake_mailcow = _broker_modules()
    reconcile.run(mailcow.MailcowClient("https://mail.example.com", fake_mailcow.API_KEY,
                                        transport=fake_mailcow.FakeMailcow().transport()),
                  db, "example.com", out=lambda _: None)
    events = db.events()
    assert {e["action"] for e in events} >= {"mailbox.create", "request.invalid", "auth.refused", "reconcile.report",
                                              "message.trace"}
    for e in events:
        jsonschema.validate(e, audit_schema, format_checker=jsonschema.FormatChecker())


# --- audit events -----------------------------------------------------------

GOOD_EVENT = {
    "schema": 1,
    "id": "01JCZ8Y4V9Q3M2K7T5R6W8X0AB",
    "time": "2026-11-15T12:00:00Z",
    "request_id": "r-123",
    "caller": "admin",
    "actor": {"kind": "admin", "id": "kc-sub-1234"},
    "source_ip": "203.0.113.7",
    "action": "account.quota",
    "operation": "setAccountQuota",
    "subject": {"account": "u-1", "address": "alice@avedeus.ovh"},
    "outcome": "ok",
    "adapter": "mailcow",
    "idempotency_key": "k" * 20,
    "changes": {"quota_mb": {"from": 2048, "to": 4096}},
}


def test_audit_schema_is_valid_and_accepts_a_normal_event(audit_schema):
    jsonschema.Draft202012Validator.check_schema(audit_schema)
    jsonschema.validate(GOOD_EVENT, audit_schema)


@pytest.mark.parametrize("bad", [
    {"password": "x"},                                    # unknown top-level field
    {"changes": {"password": {"from": "a", "to": "b"}}},  # secret-looking change
    {"changes": {"api_key": {"to": "k"}}},
    {"caller": "someone-else"},
    {"outcome": "maybe"},
    # Secrets nested inside a change value (review of PR 8): values are scalars only.
    {"changes": {"quota": {"to": {"password": "secret-value"}}}},
    {"changes": {"quota": {"from": {"nested": {"token": "x"}}}}},
    {"changes": {"quota": {"to": ["secret-value"]}}},
    {"changes": {"note": {"to": "x" * 255}}},
    {"counts": {"missing": -1}},
    {"counts": {"api_key": "abc"}},
])
def test_audit_schema_refuses_secrets_and_unknowns(audit_schema, bad):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**GOOD_EVENT, **bad}, audit_schema)


def test_audit_actions_in_contract_fit_the_schema(spec, audit_schema):
    import re
    pattern = re.compile(audit_schema["properties"]["action"]["pattern"])
    for _, _, op in operations(spec):
        if "x-avedeus-audit" in op:
            assert pattern.match(op["x-avedeus-audit"]), op["x-avedeus-audit"]
