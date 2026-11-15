# Architecture

Status: **baseline adopted 2026-11-15** (PR 8). This file describes the target
platform and, separately, what exists today. Decisions behind it are in
[docs/adr/](adr/), the machine-readable contracts in [contracts/](../contracts/),
the build order in [roadmap.md](roadmap.md), and the "baseline is done" test in
[platform-baseline.md](platform-baseline.md).

## What Avedeus is

Avedeus is a small, reusable platform that integrates and manages existing
open-source services. **Mail is its first application, not the whole
product.** It is not a new mail server: mailcow does mail, Keycloak (from
PR 11) does identity, and our own code is limited to a control plane, a few
user-facing apps and the operational glue around them.

Four rules shape every decision:

- **Foundations before experience.** Infrastructure, core services, the
  control plane and the operational foundations must be dependable before we
  invest in the experience layer.
- **Small baseline.** Every added service costs RAM and CPU on one 2-core
  Ampere box and adds maintenance forever. A service is added only when a
  milestone needs it, never because it is interesting
  ([ADR 0002](adr/0002-layered-platform.md)).
- **Use what exists first.** Before building a feature, establish whether
  mailcow, SOGo or Keycloak already provides it. Prefer enabling,
  configuring, linking and explaining existing functionality. Build new
  functionality only where there is a demonstrable gap
  ([ADR 0016](adr/0016-use-what-exists-first.md),
  [gap audit](mailcow-gap-audit.md)).
- **Make the boundaries replaceable, but don't build the replacement
  mechanism until you need it.** Before a component goes into a deployment,
  ask: can we remove it without losing a feature users actually need today?
  If yes, it does not belong in the initial deployment
  ([ADR 0017](adr/0017-minimum-complete-platform.md), which also sets the
  initial ship target).

## Layers

```
┌───────────────────────────────────────────────────────────────────────────┐
│ 5  EXPERIENCE            /join (sign-up)   /account (portal)   /admin     │
│                          password-manager tokens   personal tokens / MCP  │
│    holds: no upstream credentials, its own DB only                        │
├────────────────────────────── only via ───────────────────────────────────┤
│ 3  CONTROL PLANE         one service: callers → capabilities → adapters   │
│                          audit log · links table · jobs · reconciliation  │
│    holds: the only mailcow RW key, the Keycloak admin client              │
├───────────────────────────────────────────────────────────────────────────┤
│ 2  CORE SERVICES         mailcow (Postfix, Dovecot, Rspamd, SOGo,         │
│                          Pigeonhole, MariaDB, Redis)   Keycloak + Postgres│
├───────────────────────────────────────────────────────────────────────────┤
│ 1  INFRASTRUCTURE        Oracle A1 ARM64 (2 OCPU, 12 GB), Ubuntu 24.04,   │
│                          Docker, OCI security list, DNS (Cloudflare)      │
└───────────────────────────────────────────────────────────────────────────┘
  4  OPERATIONAL FOUNDATIONS (beside every layer): backups and restore
     drills, alerts and heartbeats, health contract, metrics, CI, update
     procedure, drift detection, runbooks, secret rotation
```

| # | Layer | Contains | Owns | Must not |
|---|---|---|---|---|
| 1 | Infrastructure | VM, OS, Docker, firewall, DNS | Host config (`scripts/setup.sh`) | Be configured by hand without a script or runbook |
| 2 | Core services | mailcow, Keycloak, Postgres server | Mailboxes and aliases (mailcow); identities (Keycloak) | Be modified inside their source trees ([ADR 0008](adr/0008-no-changes-inside-mailcow.md)) |
| 3 | Control plane | One service, grown from today's `broker/` | Caller credentials, capability table, adapters, audit log, account links, jobs | Copy upstream data it can read through ([ADR 0004](adr/0004-sources-of-truth.md)) |
| 4 | Operational foundations | Scripts, CI, monitoring, alerting, backups | Health, backups, drift reports | Need the experience layer to work |
| 5 | Experience | Sign-up, portal, admin console, tokens | Its own UI state in its own DB | Hold upstream credentials or reach a core service's admin API ([ADR 0003](adr/0003-control-plane-owns-upstream-credentials.md)) |

The research plan used three layers: *core infrastructure* (= layers 1–2),
*integration services* (= layers 3–4 plus Keycloak) and *experience* (= 5).
The five-layer split is the one we review against.

## Locked constraints

These are architectural constraints, not preferences. Every PR is reviewed
against them, and a PR that deviates must name the deviation and justify it in
its description.

1. **No upstream credentials in the experience layer.** Only the control plane
   holds mailcow, Keycloak or other service secrets.
   ([ADR 0003](adr/0003-control-plane-owns-upstream-credentials.md))
2. **No public mailbox lifecycle operations.** Password reset, suspension and
   deletion are never reachable from the public sign-up app.
   ([ADR 0010](adr/0010-no-public-lifecycle-admin-separate.md))
3. **No shared databases.** Each app has its own database, even on a shared
   Postgres server. ([ADR 0007](adr/0007-no-shared-databases.md))
4. **No changes inside the mailcow source tree.** Only supported override and
   custom files. ([ADR 0008](adr/0008-no-changes-inside-mailcow.md))
5. **Every integration is independently replaceable.** Each sits behind its
   own control-plane adapter. ([ADR 0009](adr/0009-integrations-behind-adapters.md))

**Review rule.** A PR must not quietly bring back a rejected project
([ADR 0011](adr/0011-rejected-projects.md)) or an alternative to an accepted
ADR. Every PR states two things separately: **implemented in code** (yes /
partly / no) and **proven on Oracle** (yes / no, and what was checked). Every
PR also updates the living [baseline checklist](platform-baseline.md) when it
moves a line, recording code and Oracle proof separately.

## Target architecture

```
 Internet ──443──► nginx-mailcow (mailcow's own TLS/ACME)
                    ├ /            mailcow UI, SOGo (/SOGo)
                    ├ /auth/       Keycloak                      (PR 11)
                    ├ /join/       sign-up app                   (today)
                    ├ /account/    portal                        (PR 14)
                    └ /admin/      admin console                 (PR 15)
                                   │
           experience containers ──┤ avedeus-edge (internal)
                                   ▼
                          control plane  ──RW key──► mailcow API
                                │        ──admin──► Keycloak Admin REST
                                └── own DB (links, audit, jobs)
 monitoring ──RO key──► mailcow API          mailcow ──IdP sync──► Keycloak
```

- Users sign in with Keycloak (OIDC). mailcow syncs mailboxes from Keycloak;
  SOGo signs in through mailcow and needs no provisioning of its own.
- Every caller of the control plane has its own credential and a fixed list
  of operations ([capabilities](#caller-capabilities)).
- Monitoring reads mailcow with its own read-only key and calls nothing in
  the control plane except `/health`.
- SimpleLogin, if ever adopted ([ADR 0005](adr/0005-native-aliases-first.md)),
  sits beside Keycloak and is reached only through the control plane.

### Control plane scope

The control plane is a narrow security and integration broker, not a
miniature enterprise platform ([ADR 0017](adr/0017-minimum-complete-platform.md)).
It needs caller authentication and authorisation, narrow adapters for the
integrations in use, one authoritative audit trail, idempotent operations,
basic scheduled reconciliation and health reporting. It does not initially
get a workflow engine, an event bus, a plugin framework, a distributed job
system or abstractions for integrations we haven't identified.

Keycloak (PR 11) stays the adopted identity provider but passes a
[validation gate](adr/0006-keycloak-identity-provider.md#validation-gate-before-deployment-added-2026-11-15)
before it is deployed.

### Caller capabilities

The control plane decides what each caller may do; the caller cannot widen it.
The machine-readable version is
[contracts/capabilities.yaml](../contracts/capabilities.yaml), and CI checks
that every operation it lists exists in the
[OpenAPI contract](../contracts/control-plane.openapi.yaml). Enforcement in
code since PR 9: each enforced caller has its own token and source addresses,
and may use only its row's implemented operations.

| Caller | Credential | Allowed operations | Identity of the subject comes from |
|---|---|---|---|
| `signup` (public `/join`) | Bearer token + source IP | Check address; create mailbox from an invitation (from PR 12: redeem invitation, which creates the Keycloak user, then the mailbox) | The invitation, never a request field |
| `portal` (`/account`) | Service credential + the user's Keycloak access token | The signed-in user's own aliases, rules, privacy settings, notifications | The verified Keycloak token (`sub`), never a request field |
| `admin` (`/admin`) | Service credential + admin's Keycloak token in the admin group, TOTP, allowed IP | Account lifecycle, invitations, audit read | The verified Keycloak token |
| `pm-token` (password managers) | Per-user token, hashed at rest | Create a random alias for the token's owner | The token's owner |
| `personal-token` / MCP (M10) | Per-user scoped, IP-limited, short-lived token | A subset of the `portal` row, read-only first | The token's owner |
| `monitoring` | Bearer token + source IP (`health-check.sh` via `broker.probe`, in-container) | `/v1/health`, `/v1/upstream` only | n/a |

### Data ownership

| Data | Owner | Others may |
|---|---|---|
| Mailboxes, aliases, quotas, app passwords, Sieve | mailcow | Read and change only through the control plane's mailcow adapter |
| Identities, groups, MFA, sessions | Keycloak | Read and change only through the control plane's Keycloak adapter |
| Links (Avedeus user id ↔ Keycloak `sub` ↔ mailbox), audit log, job state | Control plane, own DB | Read through control-plane operations |
| Invitations, sign-up event log | Sign-up app, own SQLite | Nothing else reads it |

The control plane reads through to the owner rather than caching copies, and a
nightly reconciliation (PR 31) reports drift between Keycloak, mailcow and the
links table. See [ADR 0004](adr/0004-sources-of-truth.md).

### Audit

Every state-changing control-plane operation, and every refused call, writes
one append-only event in the shape of
[contracts/audit-event.schema.json](../contracts/audit-event.schema.json).
Events never contain passwords, tokens or message content.

### Networks

| Network | Members | Rule |
|---|---|---|
| mailcow's own (`172.22.1.0/24`) | mailcow containers only | Postfix trusts this subnet to relay without auth, so **nothing of ours joins it** (audit fix 6). |
| `avedeus-edge` (`172.30.240.0/24`, internal) | nginx-mailcow `.2`, sign-up `.10`, control plane `.11`; later portal, admin, Keycloak | No route to the internet. The control plane reaches mailcow's API through nginx; `API_ALLOW_FROM` lists only `.11`. |

From PR 11 the experience containers may get a separate internal network that
can reach only the control plane and Keycloak; that split is decided in PR 11
and recorded as an ADR then.

### Resource budget (Oracle A1, 2 OCPU / 12 GB)

| Component | RAM (approx., to be measured on Oracle) |
|---|---|
| mailcow with ClamAV | 4–5 GB |
| Keycloak + Postgres | 1.2 GB (capped JVM heap) |
| Prometheus + Grafana + exporters (PR 27, deferred until justified) | 0 GB now (0.5 GB if added) |
| Avedeus services (control plane, sign-up, portal, admin) | 0.4 GB |
| Headroom | about 5 GB |

CPU is the tighter limit. Every service we run gets a memory limit in compose
and an arm64 image, or it is not added.

## What exists today (2026-11-15)

Implemented in code (PR #1 audit fixes, PR 9 control plane core); **nothing
is proven on Oracle yet.**

```
 Internet ──► nginx-mailcow ── /       mailcow UI, SOGo
                             ── /api/v1 mailcow API (API_ALLOW_FROM = .11)
                             ── /join/  registration:8000
                                ▲ avedeus-edge (internal, 172.30.240.0/24)
 /opt/MailServer/infrastructure/registration (separate compose project)
   ├ registration  .10  FastAPI + SQLite; no mailcow key; asks the broker
   └ broker        .11  the control plane: sole holder of the mailcow key;
                        per-caller tokens, check + create + health only,
                        own SQLite (audit log, idempotency) in its own volume
 backups ──rclone crypt──► off-site
```

| Piece | Source | Layer | Becomes |
|---|---|---|---|
| mailcow-dockerized | upstream, `/opt/mailcow-dockerized` | 2 | unchanged |
| Sign-up site + invite admin | `registration/` | 5 | sign-up only; admin pages move to `/admin` in PR 15 |
| Control plane (the broker) | `broker/` | 3 | grows one narrow adapter per integration actually in use |
| `/join/` proxy rule, SOGo and login branding | `infrastructure/mailcow/configuration/` | 2 (supported custom files) | unchanged |
| Backups, restore, health, alerts | `scripts/` | 4 | extended in M9 |
| CI and Dependabot | `.github/` | 4 | includes the contract check (PR 8, PR 9) |
| Daily reconciliation (report only) | `scripts/reconcile.sh`, `broker/broker/reconcile.py` | 4 | compares Keycloak links too once PR 12 exists (PR 31) |

### Sign-up flow today

1. An admin creates an invitation at `/join/admin` (or the CLI). The token is
   192-bit random; only its SHA-256 is stored.
2. The user opens `https://mail.<domain>/join/i/<token>` and chooses a
   username and password.
3. The app validates the input, **claims** the invitation (atomic update),
   then asks the broker to create the mailbox. The broker re-checks the rules,
   checks the address is neither a mailbox nor an alias, and calls mailcow's
   `POST /api/v1/add/mailbox`.
4. Success marks the invitation redeemed. Failure releases the claim and logs
   it for the admin; the invitation stays usable.
5. The user is sent to SOGo.

The mailbox password goes straight to mailcow and is never stored. From PR 12
the flow creates the Keycloak user first; mailcow then creates the mailbox
from Keycloak.

### Why no Caddy

mailcow's nginx already terminates TLS with Let's Encrypt for `mail.<domain>`.
Every Avedeus app is a path on the same host, so a second proxy would only add
another certificate manager and port conflicts.

### Server checks still open

- Networking: published ports still work; from the sign-up container,
  `postfix-mailcow:25`, Redis and the internet are unreachable; the broker
  reaches `https://nginx-mailcow/api/v1`.
- Rspamd `/metrics` reachable on mailcow's internal network (PR 27, deferred).
- Keycloak start-up time and steady RAM/CPU on two Ampere cores (PR 11).
- Whether mailcow offboarding or a Keycloak disable ends live SOGo and IMAP
  sessions (PR 16).
- Every native mailcow/SOGo default we rely on (gap audit O1) works on ARM64
  and survives a mailcow update.
- How many days of Postfix and Rspamd history mailcow's Redis log window
  (10,000 lines) covers on our traffic; this sets PR 34's look-back.
