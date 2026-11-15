# 0006. Keycloak is the identity provider

- Status: Accepted (2026-11-15). Plan decision E1.
- Implemented: No (PR 11 deploys it, PR 12 connects mailcow). Proven on
  Oracle: No. Open server check: start-up time and steady RAM/CPU on two
  Ampere cores beside mailcow.

## Context

mailcow's built-in Identity Provider feature supports Keycloak, LDAP or
generic OIDC. Only with Keycloak or LDAP does it do background import, a
15-minute full sync, offboarding (login off, mail still received) and group
sync with a safety brake. Keycloak's "Mailpassword Flow" also lets IMAP and
SMTP clients log in with the Keycloak password. With generic OIDC (Authelia,
Authentik, Zitadel, Kanidm, Pocket ID) mailcow only creates the mailbox at
first login.

## Decision

- Keycloak is the platform's identity provider: users, groups, MFA,
  password reset, sessions. Every Avedeus app signs in with it.
- It runs with official multi-arch images, a capped JVM heap and a memory
  limit, with its own database on a shared Postgres server
  ([0007](0007-no-shared-databases.md)), served at `/auth/` through mailcow's
  nginx, admin console IP-restricted.
- Only the control plane holds a Keycloak admin client
  ([0003](0003-control-plane-owns-upstream-credentials.md)).

## Consequences

- About 0.6–1 GB RAM plus Postgres. If it does not fit on Ampere, the
  fallback is a **Kanidm spike** through mailcow's LDAP sync. That is a spike,
  not a plan, and it would need a superseding ADR.
- mailcow's own OAuth2 provider is not used: it lacks OIDC discovery.
- How mail clients authenticate is open: [0015](0015-mail-client-passwords.md).
