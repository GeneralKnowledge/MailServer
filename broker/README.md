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
| `signup` (the registration app) | `SIGNUP_TOKEN` + `SIGNUP_CLIENTS` | `POST /v1/addresses/check`, `POST /v1/mailboxes` |
| `monitoring` (optional, unused until PR 13) | `MONITORING_TOKEN` + `MONITORING_CLIENTS` | `GET /v1/health`, `GET /v1/upstream` |
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
`unavailable` or `rejected` (502, from mailcow).

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
`python -m broker.selfcheck` (used by `health-check.sh`) checks the mailcow
key from inside the container.

## Development

```sh
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

The tests use `tests/fake_mailcow.py`. The registration app's tests also run
this broker in-process, and `contracts/tests` probes it against the contract.
Against the real mailcow API it still needs the server rehearsal.
