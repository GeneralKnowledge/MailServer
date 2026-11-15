# 0009. Every integration sits behind its own adapter

- Status: Accepted (2026-11-15). Locked constraint 5.
- Implemented: Partly. `broker/broker/mailcow.py` is the mailcow client; the
  formal adapter interface arrives in PR 9. Proven on Oracle: No

## Decision

Each upstream service (mailcow, Keycloak, later ManageSieve, SimpleLogin) is
reached through one adapter in the control plane, following the
[integration contract](../integration-contract.md). In short, an adapter:

- exposes typed operations, never a pass-through to the upstream API;
- reports health in the shared health shape;
- accepts an idempotency key on every state-changing operation;
- maps upstream errors to the control plane's error kinds;
- owns its upstream credential, read from its own env file;
- has a fake for tests, in the repository.

Callers see control-plane operations, never which adapter served them.

## Consequences

- Replacing an integration (for example Keycloak with Kanidm, or adding
  SimpleLogin) changes one adapter and its config, and leaves user
  management, webmail and the other integrations alone.
- No app may call an upstream service directly "just this once".
