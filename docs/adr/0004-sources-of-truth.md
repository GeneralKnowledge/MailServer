# 0004. Sources of truth: mailcow for mail, Keycloak for identity

- Status: Accepted (2026-11-15)
- Implemented: Partly. The broker reads mailcow live and stores nothing.
  Keycloak and the links table come with PR 9 and PR 12. Proven on Oracle: No

## Context

Several services know about "a user": mailcow (mailbox), Keycloak (identity),
the sign-up app (invitation), later SimpleLogin. Copies drift.

## Decision

| Data | Owner |
|---|---|
| Mailboxes, aliases, quotas, app passwords, Sieve scripts | mailcow |
| Identities, credentials, groups, MFA, sessions | Keycloak |
| Links between them (Avedeus user id ↔ Keycloak `sub` ↔ mailbox address), audit log, job state | Control plane, in its own database |
| Invitations | Sign-up app, in its own database |

The control plane reads through to the owner instead of caching copies. A
nightly reconciliation (PR 31) reports drift between Keycloak, mailcow and the
links table, and fixes only cases marked safe.

mailcow has no webhooks or event stream, so changes are found by polling and
reconciliation, not events.

## Consequences

- No feature may introduce a second copy of mailbox or identity data as a
  source of truth.
- Restoring one store can create drift; reconciliation reports it after a
  restore drill.
