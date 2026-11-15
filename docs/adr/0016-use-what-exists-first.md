# 0016. Use what mailcow, SOGo and Keycloak already provide first

- Status: Accepted (2026-11-15, set by the owner after the
  [mailcow feature-gap audit](../mailcow-gap-audit.md))
- Implemented: n/a (a review rule). Proven on Oracle: n/a

## Context

The gap audit checked the planned user features against mailcow's source and
docs. Most of them already exist: protocol-scoped app passwords, two-factor
sign-in, login history, spam thresholds and allow/block lists, quarantine with
digests, mail import, a visual filter editor in SOGo, and sessions, MFA and
password reset in Keycloak. Rebuilding them would cost RAM, CPU and
maintenance on a 2-core box and give users two places to do the same thing.

## Decision

Before building a feature, establish whether mailcow, SOGo or Keycloak already
provides it. Prefer enabling, configuring, linking and explaining existing
functionality. Build new functionality only where there is a demonstrable gap.

In practice, every PR that adds a user-facing or admin-facing feature says in
its description:

1. what mailcow, SOGo and Keycloak already offer for it (with a source link
   or file path), and
2. why enabling, configuring, linking or explaining that is not enough.

## Consequences

- Avedeus makes the existing functionality accessible, coherent and properly
  explained, then fills the genuine gaps. It does not put a second interface
  over things that already work.
- Configuration defaults we rely on are verified on Oracle, not assumed
  (see the "native defaults" server check in the [roadmap](../roadmap.md)).
- Fits [0002](0002-layered-platform.md) (small baseline) and
  [0008](0008-no-changes-inside-mailcow.md): "enable" means mailcow's
  supported settings and custom files, never edits inside its tree.
