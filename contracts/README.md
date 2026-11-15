# Contracts

Machine-readable service contracts for the Avedeus platform
([ADR 0003](../docs/adr/0003-control-plane-owns-upstream-credentials.md),
[0009](../docs/adr/0009-integrations-behind-adapters.md),
[0010](../docs/adr/0010-no-public-lifecycle-admin-separate.md)).

| File | What it fixes |
|---|---|
| [control-plane.openapi.yaml](control-plane.openapi.yaml) | Every control-plane operation, its request/response shape, error envelope, and whether it is `implemented` (exists in `broker/` now) or `planned` (with the PR that builds it). |
| [capabilities.yaml](capabilities.yaml) | Which caller may use which operation. The control plane enforces it from PR 9. |
| [audit-event.schema.json](audit-event.schema.json) | The one shape of every audit event. Written from PR 9. |

The adapter side (how the control plane talks to mailcow, Keycloak and later
services) is described in [docs/integration-contract.md](../docs/integration-contract.md).

## Checked in CI

`contracts/tests/test_contracts.py` runs in the **Contracts** CI job. It fails
when:

- the broker gains or loses a route that the contract does not list as
  `implemented`, or a real broker response does not match the contract's
  schema;
- an operation is granted to nobody, or a caller is granted an operation that
  does not exist;
- the public `signup` caller is granted any lifecycle, alias or audit
  operation, or anyone but `admin` gets lifecycle or audit;
- a planned state-changing operation has no `Idempotency-Key` or no audit
  action;
- the audit schema would accept a password, token or key, including one
  nested inside a change value;
- the enforced capability rows differ from the broker's copy, or the running
  broker lets a caller reach an operation its row doesn't grant (or refuses
  one it does);
- the sign-up app's real requests don't match the request schemas, the broker
  accepts a body the schema refuses, or an audit event the broker writes
  doesn't match the audit schema.

```sh
pip install -r broker/requirements.txt -r contracts/requirements-dev.txt
python -m pytest -q contracts
```

## Changing a contract

- Adding or changing an **implemented** operation: change the broker and the
  contract in the same PR; CI enforces it.
- Changing a **planned** operation's details: fine in the PR that builds it.
- Changing **who may call** an operation (capabilities.yaml), or giving the
  experience layer anything new: an architecture change. Name it in the PR
  description and justify it (review rule).

## Resolved in PR 9

- Request-validation errors now use the error envelope (`kind: invalid`) and
  name only the fields at fault; they never echo the request. The sign-up app
  also no longer keeps the body of any non-envelope 422 it receives.
- Each caller has its own credential and source addresses, and the
  `enforced: true` rows are checked on every call. CI compares them with
  `broker/broker/capabilities.py` and probes the running broker as every
  caller.
- CI checks that the sign-up app's real requests match the request schemas,
  that the broker refuses bodies the schemas refuse, and that every audit
  event it writes matches the audit schema.
