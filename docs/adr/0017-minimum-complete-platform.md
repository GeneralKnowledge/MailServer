# 0017. Minimum complete platform; build only what current integrations need

- Status: Accepted (2026-11-15, set by the owner in the scope-reduction pass
  after the [mailcow feature-gap audit](../mailcow-gap-audit.md))
- Implemented: n/a (scope and review rules). Proven on Oracle: n/a

## Context

The architecture ([0002](0002-layered-platform.md)) and the five locked
constraints stay as adopted. The risk is no longer rebuilding mailcow
features ([0016](0016-use-what-exists-first.md) covers that) but building
more infrastructure and abstraction than a one-person service on a 2-OCPU
box needs. The aim is a relatively small amount of custom software that
unlocks everything mailcow already does, not another large platform to
maintain.

## Decision

**Rule 1. Make the boundaries replaceable, but don't build the replacement
mechanism until you need it.** Constraint 5 is met by one narrow adapter per
integration actually in use, with a fake for tests. It is not a universal
adapter framework, a plugin system, or adapters for integrations nobody has
chosen. A standard audit format is sensible; an event-processing architecture
built for applications we might have one day is not.

**Rule 2. The removal test.** Before a component goes into a deployment, ask:
*can we remove it without losing a feature users actually need today?* If
yes, it does not belong in the initial deployment. Every PR that adds a
service or a framework answers this in its description.

**The control plane stays a narrow security and integration broker.**

| It needs | It does not initially need |
|---|---|
| Authentication and authorisation of callers | A general-purpose workflow engine |
| Narrow service adapters for the integrations in use | A sophisticated event bus |
| Audit logging (one authoritative trail) | A generic plugin framework |
| Idempotent operations | A complex distributed job system |
| Basic reconciliation (a simple scheduled job) | Abstractions for integrations we haven't identified |
| Health reporting | |

**Initial ship target: Avedeus Mail, minimum complete platform.**

1. *Reliable mail infrastructure*: mailcow, SOGo, DNS, spam filtering,
   backups and tested restoration.
2. *Safe identity*: Keycloak integration (subject to its validation gate,
   [0006](0006-keycloak-identity-provider.md)), account lifecycle, proper
   credential separation.
3. *A small control plane*: provisioning, permissions, audit and
   reconciliation.
4. *A useful portal*: one place to find account settings, security links,
   administration and existing mailcow functionality.
5. *Operational intelligence*: alerts, delivery diagnostics and recovery
   tools.

Everything else earns its place through demonstrated demand.

**Not cut** (expensive to retrofit, done properly now): backup and tested
restoration, network isolation, the provisioning broker, credential
separation, audit logging, idempotent mailbox operations, identity and
account lifecycle boundaries, ARM64 compatibility and resource limits.

**Outside the initial commitment** (deferred until demand is shown):
SimpleLogin; a visual Sieve editor; cross-service automation; a custom
security dashboard; a user-facing message trace; an account export
application; personal API tokens and the MCP server; the AI assistant; the
Prometheus/Grafana metrics stack.

## Consequences

- The [roadmap](../roadmap.md) marks PR 27, 32 and 33 deferred and keeps
  PR 31 a simple scheduled job. PR numbers are unchanged.
- Monitoring starts from mailcow's own (watchdog, UI, logs) plus our alerts
  (audit fix 5), backup verification, the delivery probe and the message
  trace. Dashboards come when an operational need appears.
- Keycloak stays the adopted identity provider but must pass a validation
  gate before it is deployed (PR 11).
- This does not change the architecture or the five locked constraints.
