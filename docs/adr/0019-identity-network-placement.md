# 0019. Where Keycloak and its database sit on the network

- Status: **Accepted** (2026-11-15, PR 11). Answers the network question
  [architecture.md](../architecture.md#networks) left to PR 11.
- Implemented: Yes in code (`infrastructure/identity/docker-compose.yml`,
  checked in CI). Proven on Oracle: No (not deployed; ADR 0006 gate).

## Context

Keycloak must be reachable by mailcow's nginx (to serve `/auth/`) and by the
control plane (its Keycloak adapter, PR 12). Its database must not be
reachable by anything else ([0007](0007-no-shared-databases.md)). The
architecture left open whether experience containers (portal, admin) get a
separate internal network that reaches only the control plane and Keycloak.

## Decision

- **Keycloak** joins `avedeus-edge` at the fixed address `172.30.240.12`,
  beside the sign-up app (`.10`) and the control plane (`.11`). No ports are
  published; the public path is `https://<mail host>/auth/` through mailcow's
  nginx.
- **Postgres** is Keycloak's own server and database, on a separate internal
  network `identity-db` whose only other member is Keycloak. It is not on
  `avedeus-edge` and has no route out. ADR 0006 described a Postgres server
  that might later hold other databases; PR 11 starts with a dedicated one,
  which is stricter, and nothing else is planned to use it.
- **No separate experience network yet.** No experience container exists;
  splitting the network now would be machinery without a user
  ([0017](0017-minimum-complete-platform.md)). PR 14 (portal) or PR 15
  (admin), whichever comes first, decides it with a real container to place.
- Keycloak's health and metrics endpoints are on its management port (9000),
  which nginx never proxies. Metrics stay off (PR 27 deferred).

## PR 14 addendum: no separate experience network (2026-11-15)

The portal (`172.30.240.13`) is the first experience container, so PR 14
decides the deferred question: **it joins `avedeus-edge`; no separate
network.** What a split would protect is already protected without it: the
portal holds no upstream credential; mailcow's API accepts only `.11`;
Keycloak's admin API needs a client credential the portal doesn't have; and
the control plane accepts the portal's token only from `.13` and the user
only from a verified Keycloak token. A split would add a network and a
second nginx path for no new protection (ADR 0017). Revisit if an
experience container ever needs a credential or a port that the others
must not reach.

## Consequences

- The sign-up app can reach Keycloak's port on `avedeus-edge`. It holds no
  Keycloak credential, and only the control plane's client may change users,
  so this gives it no new power; PR 14/15 revisits the split.
- Removal test ([0017](0017-minimum-complete-platform.md)): stopping the
  identity stack and deleting `site.identity.custom` removes Keycloak
  entirely; mailcow and the sign-up flow keep working on mailcow's own
  authentication.
