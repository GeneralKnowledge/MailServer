# Control plane (provisioning broker)

The only component that holds the mailcow API key (ADR 0003). It is a
**narrow** security and integration broker (ADR 0017): caller
authentication and capabilities, one adapter per integration in use, an
append-only audit log, idempotency keys, health and a report-only
reconciliation job. No workflow engine, event bus, plugins or job system.

The contract is [contracts/](../contracts/): the
[OpenAPI file](../contracts/control-plane.openapi.yaml), the
[capability table](../contracts/capabilities.yaml) and the
[audit schema](../contracts/audit-event.schema.json). CI checks this code
against all three.

## Callers and what they may do

| Caller | Credential (broker.env) | Operations |
|---|---|---|
| `signup` (the registration app) | `SIGNUP_TOKEN` + `SIGNUP_CLIENTS` | `POST /v1/addresses/check`, `POST /v1/accounts` (PR 12), `POST /v1/mailboxes` (refused while Keycloak is on) |
| `monitoring` (`health-check.sh` via `broker.probe`) | `MONITORING_TOKEN` + `MONITORING_CLIENTS=127.0.0.1` | `GET /v1/health`, `GET /v1/upstream` |
| `portal` (the account portal, PR 14) | `PORTAL_TOKEN` + `PORTAL_CLIENTS=172.30.240.13`, plus the signed-in user's Keycloak access token in `X-User-Token` (signature, issuer, expiry and `azp` verified against the realm's keys) | `GET /v1/me` (the user's own address, name, quota, state; the user comes only from the token) |
| `admin` (the admin console, PR 15; `python -m broker.trace` inside the container) | `ADMIN_TOKEN` + `ADMIN_CLIENTS=127.0.0.1,172.30.240.14`, plus from the console the admin's Keycloak access token in `X-User-Token` (signature, issuer, expiry, `azp=avedeus-admin`, `admins` group, `otp` in `amr`); from `127.0.0.1` the token alone (break-glass, ADR 0020) | `GET /v1/trace` (read-only message trace, PR 34; uses `MAILCOW_READ_ONLY_API_KEY` only), `GET /v1/accounts` (read-only list, PR 15), `GET /v1/audit` (recent events, PR 15) |
| anyone | none | `GET /healthz` (liveness) |

Each caller presents `Authorization: Bearer <its token>` from one of its own
addresses. Every token must be different. A valid token for the wrong
operation gets 403. The table lives in `broker/capabilities.py`; CI fails if
it differs from `contracts/capabilities.yaml`.

Whatever the caller sends, the control plane enforces its own rules:

- the domain is fixed (`MAIL_DOMAIN`); unknown request fields are refused;
- username format and reserved names (`postmaster`, `abuse`, `admin`, …);
- quota between 1 and `MAX_QUOTA_MB`;
- at most `MAX_CREATES_PER_DAY` new mailboxes in 24 hours, counted from the
  audit log, so a restart doesn't reset it (failures don't count).

Errors are always `{"ok": false, "kind": …, "detail": …}` and never echo the
request. Kinds: `invalid`/`password` (422), `taken`/`conflict` (409), `limit`
(429), `unauthorized` (401), `forbidden` (403), `not_found` (404), and `auth`,
`unavailable` or `rejected` (502, from mailcow or Keycloak).

## One identity (PR 12)

With `IDENTITY_PROVIDER=keycloak`, `POST /v1/accounts` creates the Keycloak
user first (via `broker/keycloak.py`, the only Keycloak credential on the
box), records the link, then creates the mailbox with Keycloak as its login
source. Default `none` keeps mailcow's own login. Details, failure handling
and the mailcow settings: [docs/identity.md](../docs/identity.md#one-identity-pr-12).

## Audit log and idempotency

Its own SQLite database (`CONTROL_PLANE_DB`, default
`/data/control-plane.sqlite3` in the `control-plane-data` volume) holds:

- **audit_events**, append-only (triggers refuse UPDATE and DELETE): every
  mailbox creation, every refused call (bad token, wrong caller, invalid
  request, rule or limit), every reconciliation run. No passwords, tokens or
  message content.
- **idempotency**: `POST /v1/mailboxes` accepts an `Idempotency-Key`. A
  repeat with the same key and request returns the first answer without
  calling mailcow again; the same key with a different request is a 409
  `conflict`; a failed attempt leaves no record, so it can be retried. The
  stored fingerprint excludes the password. Records are kept 7 days.

`scripts/backup.sh` copies the database (`python -m broker.backup`) and
`scripts/restore.sh registration` restores it.

## Reconciliation

`scripts/reconcile.sh` (daily cron) runs `python -m broker.reconcile`: it
compares the mailboxes the control plane created with mailcow's list, writes
a `reconcile.report` audit event, alerts if one we created is gone, and lists
mailboxes made outside the control plane as "unmanaged". It changes nothing.

## Running

It is the `broker` service in
[infrastructure/registration/docker-compose.yml](../infrastructure/registration/docker-compose.yml),
configured by `broker.env` ([example](../infrastructure/registration/broker.env.example)).
It is on the internal `avedeus-edge` network at `172.30.240.11` and publishes
no port. mailcow's `API_ALLOW_FROM` must list only that address.
`scripts/health-check.sh` runs `python -m broker.probe` in the container: it
calls `GET /v1/health` from 127.0.0.1 as the monitoring caller and prints one
line per component, and the health check alerts once per component that
goes degraded or down (and once when it recovers). An adapter answering
slower than 5 s counts as degraded. `python -m broker.selfcheck` remains for
a manual check of the mailcow key.

## Development

```sh
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

The tests use `tests/fake_mailcow.py`. The registration app's tests also run
this broker in-process, and `contracts/tests` probes it against the contract.
Against the real mailcow API it still needs the server rehearsal.
