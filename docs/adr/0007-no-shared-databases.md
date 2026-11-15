# 0007. No shared databases between applications

- Status: Accepted (2026-11-15). Locked constraint 3.
- Implemented: Yes so far (sign-up app has its own SQLite; the broker has no
  database; nothing of ours touches mailcow's MariaDB). Proven on Oracle: No

## Decision

- Each application has its own database and its own credentials for it. One
  Postgres **server** may host several databases (Keycloak, control plane,
  SimpleLogin if adopted), but no app gets credentials for another's database.
- No app reads or writes mailcow's MariaDB or Redis. mailcow data is reached
  only through mailcow's API, through the control plane.
- Data another app needs is exposed as a control-plane operation.

## Consequences

- Any component can be replaced or restored without schema coupling.
- Backups list each database separately so each can be restored alone.
