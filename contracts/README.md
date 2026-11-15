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
- the audit schema would accept a password, token or key.

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

## Known differences (to fix in PR 9)

- FastAPI's own request-validation errors (missing field, wrong type, password
  shorter than 8 or longer than 128) come back as FastAPI's default
  `{"detail": [...]}` instead of the error envelope, and that default echoes
  the request body, password included. With the sign-up app's defaults
  (passwords 12–128 characters, checked before the call) this is not
  reachable, but the sign-up app stores that `detail` in its event log, so it
  would become a leak if `MIN_PASSWORD_LEN` were set below 8. PR 9 should
  return the envelope and never echo input.
- One `BROKER_TOKEN` is shared by the `signup` and `monitoring` rows today.
  Per-caller credentials arrive in PR 9.
