# 0002. A small layered platform; mail is the first application

- Status: Accepted (2026-11-15)
- Implemented: Partly (layers 1–4 exist in early form). Proven on Oracle: No

## Context

Avedeus started as "an invite-only mailcow". The owner's direction is
broader: a reusable platform that integrates existing open-source services,
with mail as its first application. It runs on one Oracle A1 instance
(2 Ampere cores, 12 GB RAM), where mailcow alone takes 4–5 GB.

## Decision

The platform has five layers:

1. **Infrastructure**: VM, OS, Docker, firewall, DNS.
2. **Core services**: mailcow; Keycloak and its Postgres (from PR 11).
3. **Control plane**: one service that holds upstream credentials, enforces
   caller capabilities, runs adapters, writes the audit log.
4. **Operational foundations**: backups and restore drills, alerts, health,
   metrics, CI, update procedure, drift detection, runbooks.
5. **Experience**: sign-up, portal, admin console, tokens.

Layers 1–4 must meet the [baseline checklist](../platform-baseline.md) before
we invest further in layer 5.

**Service admission rule.** A new service (container) is added only when a
roadmap milestone needs it, and only with: an arm64 image, a memory limit, a
place in backups (or a reason it has none), a health check, an adapter in the
control plane if anything calls it, and an ADR if it is a new upstream
project. "Interesting" is not a reason.

## Consequences

- New apps (beyond mail) reuse identity, the control plane and the
  operational foundations instead of bringing their own.
- The first year adds at most two upstream projects: Keycloak, and later,
  optionally, SimpleLogin ([0005](0005-native-aliases-first.md)).
- Some useful tools are deliberately left out (cAdvisor, a second webmail,
  Nextcloud) because of CPU and maintenance cost.
