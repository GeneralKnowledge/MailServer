"""Keep the architecture contracts consistent with each other and with the code.

- control-plane.openapi.yaml is well formed and every operation says whether
  it is implemented or planned (and for which PR);
- capabilities.yaml grants only operations that exist, grants every
  operation to someone, and never gives the public sign-up caller lifecycle,
  alias or audit operations (ADR 0010);
- the broker's real routes are exactly the contract's implemented operations,
  and its real responses match the contract's schemas;
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


def test_monitoring_gets_health_only(spec, caps):
    ops = by_id(spec)
    for op_id in caps["callers"]["monitoring"]["operations"]:
        assert ops[op_id][2].get("tags") == ["health"], op_id


def test_tokens_cannot_exceed_portal(caps):
    portal = set(caps["callers"]["portal"]["operations"])
    assert set(caps["callers"]["personal-token"]["operations"]) <= portal


# --- the broker matches the contract ----------------------------------------

@pytest.fixture(scope="module")
def broker():
    sys.path.insert(0, str(ROOT / "broker"))
    sys.path.insert(0, str(ROOT / "broker" / "tests"))
    from fastapi.testclient import TestClient

    from broker.config import load_settings
    from broker.mailcow import MailcowClient
    from broker.main import create_app
    from fake_mailcow import API_KEY, FakeMailcow

    token = "c" * 40
    settings = load_settings({
        "MAILCOW_API_URL": "https://mail.example.com", "MAILCOW_API_KEY": API_KEY,
        "MAIL_DOMAIN": "example.com", "BROKER_TOKEN": token, "BROKER_ALLOWED_CLIENTS": "*",
        "MAX_CREATES_PER_DAY": "1",
    })
    fake = FakeMailcow()
    mc = MailcowClient(settings.mailcow_api_url, settings.mailcow_api_key, transport=fake.transport())
    app = create_app(settings, mailcow=mc)
    return app, TestClient(app), {"Authorization": f"Bearer {token}"}


def test_broker_routes_equal_implemented_operations(spec, broker):
    app = broker[0]
    routes = {(m, r.path) for r in app.routes if getattr(r, "methods", None)
              for m in r.methods if m.lower() in HTTP_METHODS}
    implemented = {(m, p) for m, p, op in operations(spec) if op["x-avedeus-status"] == "implemented"}
    assert routes == implemented, (
        f"in broker but not in contract: {routes - implemented}; "
        f"in contract but not in broker: {implemented - routes}"
    )


def _check(spec, op_id, response):
    _, _, op = by_id(spec)[op_id]
    declared = op["responses"].get(str(response.status_code))
    assert declared, f"{op_id}: status {response.status_code} not in contract"
    schema = resolve(declared, spec)["content"]["application/json"]["schema"]
    jsonschema.validate(response.json(), schema)


def test_broker_responses_match_contract(spec, broker):
    _, client, auth = broker
    _check(spec, "healthz", client.get("/healthz"))
    _check(spec, "checkAddress", client.post("/v1/addresses/check", json={"local_part": "carol"}, headers=auth))
    _check(spec, "checkAddress", client.post("/v1/addresses/check", json={"local_part": "admin"}, headers=auth))
    body = {"local_part": "carol", "password": "Sunny-River-42x", "name": "C", "quota_mb": 1024}
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json=body, headers=auth))            # 201
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json=body, headers=auth))            # 429 cap
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json={**body, "quota_mb": 0}, headers=auth))  # 422
    _check(spec, "createMailbox", client.post("/v1/mailboxes", json=body))                          # 401
    _check(spec, "upstreamCheck", client.get("/v1/upstream", headers=auth))


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
