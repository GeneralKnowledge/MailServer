# Integration contract

What every integration (adapter) in the control plane must provide, and what
every new service must bring before it is added to the box. Background:
[ADR 0002](adr/0002-layered-platform.md) (admission rule) and
[ADR 0009](adr/0009-integrations-behind-adapters.md) (adapters).

Status: **adapter side implemented in PR 9** for mailcow (`broker/broker/adapters.py`,
`broker/broker/mailcow.py`): A1–A8 and A10 hold; A9 can only be shown when a
second integration exists. Not proven on Oracle. PR 9 built the adapter interface and
moves the existing mailcow client (`broker/broker/mailcow.py`) behind it.
**PR 12** adds the second adapter, Keycloak (`broker/broker/keycloak.py`):
A1–A8 and A10 hold (fake in `broker/tests/fake_keycloak.py`; also run against
a real Keycloak in CI). A9 holds partly: the account flow in `main.py` names
mailcow's `keycloak` login source and the `mailcow_template` attribute, so a
different identity provider would change those two lines as well as the
adapter. **PR 16** adds typed lifecycle operations to both adapters (mailcow:
set a mailbox's state or quota, delete it; Keycloak: read, disable/enable,
sign out, require a new password, delete), each with its fake and the
Keycloak ones run against a real Keycloak in CI. `main.py` speaks only the
adapter's state names (`active`, `login_disabled`, `inactive`); mailcow's
`active` values stay inside `mailcow.py`.

## An adapter (code in the control plane)

| # | Requirement | Why |
|---|---|---|
| A1 | **Typed operations only.** The adapter offers named operations (`create_mailbox`, `set_alias_active`, …), never "call this upstream URL". | A caller can't reach upstream features nobody reviewed. |
| A2 | **Health.** `health()` returns `{status: ok|degraded|down, detail, latency_ms}` and makes at most one cheap upstream call. Aggregated by `/v1/health` (PR 13). | One place for alerts to look. |
| A3 | **Idempotency.** Every state-changing operation accepts an idempotency key, and either is naturally idempotent upstream or checks state after a lost reply (as the broker's create already does). | Retries and lost replies must not duplicate accounts or aliases. |
| A4 | **Error mapping.** Upstream errors map to the contract's `ErrorKind` (`auth`, `unavailable`, `rejected`, `taken`, …). Upstream messages are cut to 500 characters and never contain secrets. | Callers never depend on an upstream's error format. |
| A5 | **Own credential.** The adapter reads its upstream secret from its own env file, which only the control plane container mounts. One credential per upstream, IP-locked where the upstream supports it. | ADR 0003. |
| A6 | **Timeouts.** Every upstream call has a timeout, and the sum for one control-plane operation stays below nginx's 60 s. | A slow upstream must not hang callers. |
| A7 | **Audit.** The control plane, not the adapter, writes the audit event; the adapter reports which upstream objects changed. | One audit format. |
| A8 | **A fake.** A fake upstream lives in the repo, and the adapter's tests run against it in CI. | We can build before the server exists. |
| A9 | **Replaceable.** Nothing outside the adapter imports upstream-specific types or field names. | Swapping Keycloak for Kanidm, or adding SimpleLogin, changes one adapter. |
| A10 | **Read-through.** The adapter reads the upstream (the source of truth); it does not keep its own copy except short-lived caches documented in the adapter. | ADR 0004. |

## A service (container on the box)

Before a new container is added (control plane, Keycloak, portal, …):

| # | Requirement |
|---|---|
| S1 | An arm64 image (official, or built in our CI). |
| S2 | A memory limit in compose, and its expected RAM added to the budget in [architecture.md](architecture.md#resource-budget-oracle-a1-2-ocpu--12-gb). |
| S3 | A health check (compose `healthcheck`) and a line in `scripts/health-check.sh` or the `/v1/health` aggregate. |
| S4 | Its data in backups, restorable on its own, or a written reason it has no data. |
| S5 | Its own database and credentials ([ADR 0007](adr/0007-no-shared-databases.md)). |
| S6 | Network: internal networks only, no published port unless it is served through mailcow's nginx. Never on mailcow's own network. |
| S7 | Updates: covered by Dependabot or a written update step. |
| S8 | If it is a new upstream project: an ADR, checked against [ADR 0011](adr/0011-rejected-projects.md). |

A PR that adds a service lists S1–S8 and says, for each, whether it is
implemented in code and whether it is proven on Oracle.
