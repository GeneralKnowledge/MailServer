# 0005. mailcow-native aliases first; SimpleLogin optional and gated

- Status: Accepted (2026-11-15). Plan decision E2.
- Implemented: No. Proven on Oracle: No
- **Parked (2026-11-15, owner):** PR 10, 19, 21 and 22 are not planned
  deliverables. Native mailcow aliases remain available as mailcow provides
  them; we are not building an additional alias-management product now. If
  alias work is revived, this ADR still sets its direction.

## Context

Research on 15 Nov 2026 compared SimpleLogin, addy.io, idmail and mailcow's
own aliases. mailcow already offers `add/alias`, `add/time_limited_alias`,
per-alias active flags, send-as through sender ACLs and sub-addressing.
SimpleLogin's official images are amd64-only (its Dockerfile pins
`linux/amd64`), it has no admin user API, needs its own Postfix path and
Postgres, and is AGPL-3.0. Its main benefit, hiding a mailbox hosted
elsewhere, matters less when the mailbox is already on our domain.

## Decision

- Alias management starts with **mailcow-native aliases** through the control
  plane: random and custom aliases, expiry, on/off, delete, per-user limits,
  reserved names.
- The control plane also offers **SimpleLogin- and addy-compatible endpoints**
  (`/api/alias/random/new`, `/api/v1/aliases`) behind per-user tokens, so
  Bitwarden and other password managers can create aliases. We copy the API
  shape only, not the projects.
- **SimpleLogin is optional**, not a foundation. It is considered only after
  PR 21 shows a working arm64 build and there is a real user need
  (reverse-alias replies, external mailboxes), and only with the owner's
  go-ahead. If adopted, it sits behind its own adapter and native aliases stay.

## Consequences

- No new service for aliases in the baseline.
- The alias domain is still open: [0013](0013-alias-domain.md).

## Alternatives rejected

addy.io and idmail: see [0011](0011-rejected-projects.md).
