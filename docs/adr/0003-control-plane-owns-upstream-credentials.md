# 0003. Only the control plane holds upstream credentials

- Status: Accepted (2026-11-15). Locked constraint 1.
- Implemented: Yes for mailcow: `broker/` is the only holder of the mailcow
  key and the registration app refuses to start with `MAILCOW_API_KEY` set
  (audit fix 7). Keycloak arrives in PR 11/12. Proven on Oracle: No

## Context

mailcow API keys are either read-only or read-write. A key can be IP-locked,
but it cannot be limited to certain endpoints, so any read-write key is
effectively full admin. The source-code audit found the public sign-up app
holding exactly such a key (finding H2).

## Decision

- The **control plane** is the only component holding read-write or admin
  credentials for upstream services: the mailcow read-write key, the Keycloak
  admin client, and any future integration's secrets.
- Every caller gets its own credential and a fixed list of operations
  ([contracts/capabilities.yaml](../../contracts/capabilities.yaml)). The
  control plane enforces the list and its own business rules, whatever the
  caller sends.
- The experience layer never calls mailcow or Keycloak admin APIs. It may use
  Keycloak's public OIDC endpoints to sign users in, which need no secret
  beyond its own OIDC client.
- Monitoring may hold its own **read-only** mailcow key and nothing else.
- Today's `broker/` is the first version of the control plane; PR 9 grows it.

## Consequences

- A compromised experience app can do only what its capability row allows.
- The control plane is a high-value target: it publishes no port, sits on an
  internal network, and its mailcow key is IP-locked to its address.
- Personal tokens and MCP (M10) are just more callers with their own row;
  they never get a route into mailcow.
