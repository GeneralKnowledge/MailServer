# 0006. Keycloak is the identity provider

- Status: Accepted (2026-11-15). Plan decision E1.
- Implemented: Partly. **PR 11:** Keycloak and its own Postgres as code
  (`infrastructure/identity/`), realm, clients and groups as code, `/auth/`
  through mailcow's nginx with the admin console closed by default, backup
  and restore of its database, and a start-and-import test in CI
  (`scripts/identity-smoke.sh`). **Not deployed**: the gate below is still
  open. PR 12 connects mailcow. Proven on Oracle: No. Open server check:
  start-up time and steady RAM/CPU on two Ampere cores beside mailcow
  (measured on x86 in testing: about 670 MB for Keycloak with a 512 MB heap,
  60 MB for Postgres). PR 11 runs a dedicated Postgres server rather than a
  shared one, which is stricter ([0019](0019-identity-network-placement.md)).

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

## Validation gate before deployment (added 2026-11-15)

The decision stands, but Keycloak is not deployed until it has earned its
place ([0017](0017-minimum-complete-platform.md)). mailcow already has
authentication, MFA, app passwords, recovery and user management; Keycloak
adds a service, a database, a backup requirement and an authentication
boundary. Its justification is unified identity across Avedeus apps, not
better email. PR 11 does not deploy it on Oracle until all of these hold:

1. At least one Avedeus app other than mailcow (portal, admin console) is
   ready to sign in with it, so unified identity is a need today.
2. Measured RAM and CPU on the Ampere box fit the resource budget beside
   mailcow.
3. Its database is covered by backup and a restore has been shown (PR 29
   scope).
4. Lifecycle works end to end with mailcow's IdP sync, including whether
   offboarding ends live sessions (open server check).

If the gate fails, Keycloak's integration is **paused**, not forced into
production (owner, 2026-11-15). mailcow keeps its own authentication in the
meantime, and the owner decides the next step in a superseding ADR. No
alternative is assumed here.
