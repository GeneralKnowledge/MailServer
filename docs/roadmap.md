# Roadmap: M5–M10

Adopted 2026-11-15 as the working plan (source: the "Avedeus integration
platform: research and plan" document). Replaces `docs/roadmap-m5-m7.md`;
nothing from that plan is dropped (see [mapping](#where-the-old-m5m7-items-went)).

**Order.** M0–M4 on the real Oracle server first (server setup is on hold
until the owner says go). Audit fixes 1–7 are done in code (PR #1). PR 8 (this
architecture) comes next, then PR 9 onward. Every PR:

- is small enough to review in one sitting, with tests against fakes;
- lands behind a feature flag where it touches users;
- states **implemented in code** and **proven on Oracle** separately;
- updates [platform-baseline.md](platform-baseline.md) when it moves a line
  (code and Oracle proof recorded separately), or says "baseline: no change";
- names and justifies any departure from an accepted ADR or a rejected
  project ([ADR 0011](adr/0011-rejected-projects.md)).

"Server: yes" means the PR is not done until it has run on the Oracle box.

## M5 – Integration foundation

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 8 | Architecture and service contracts | Docs and contracts only: [architecture](architecture.md), this roadmap, [ADRs](adr/), [control-plane contract](../contracts/control-plane.openapi.yaml), [capabilities](../contracts/capabilities.yaml), [audit schema](../contracts/audit-event.schema.json), [integration contract](integration-contract.md), [baseline checklist](platform-baseline.md), CI contract check | Audit 7 | No |
| 9 | Control plane core | Broker becomes the control plane: adapter interface, mailcow adapter, per-caller credentials checked against capabilities.yaml, append-only audit log, idempotency keys, error envelope for validation errors, nightly reconciliation stub | 8 | No |
| 10 | Alias back end (mailcow-native) | List, create random/custom, expire, disable, delete own aliases; per-user limits; reserved names; SimpleLogin/addy-compatible endpoints behind per-user tokens. Built against fakes; done only after an Oracle rehearsal. **Needs E3** ([ADR 0013](adr/0013-alias-domain.md)) | 9 | Yes |
| 11 | Keycloak and Postgres | arm64 images, memory caps, capped JVM heap; realm, clients, groups as code; `/auth/` through mailcow nginx; admin console IP-restricted; in backups | 8 | Yes |
| 12 | One identity | mailcow IdP set to Keycloak with sync and offboarding; mailbox templates; migration of existing mailboxes; Keycloak adapter; invitation creates the Keycloak user, then the mailbox. **Needs E5** ([ADR 0015](adr/0015-mail-client-passwords.md)) | 9, 11 | Yes |
| 13 | Health contract | Every adapter reports health; one internal `/v1/health` feeds the audit-5 alerts | 9 | No |

## M6 – User experience

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 14 | Portal shell | `/account`, Keycloak sign-in; quota, aliases, app passwords, CalDAV/CardDAV setup, link to SOGo | 12 | Yes |
| 15 | Admin console split | Admin pages leave the sign-up app for their own container at `/admin`; Keycloak admin group + TOTP + IP allow-list; accounts list. **Open design point:** invitations live in the sign-up app's own SQLite today; because databases are not shared ([ADR 0007](adr/0007-no-shared-databases.md)), PR 15 must either move invitations into the control plane or have the sign-up app expose them through an internal API, and record the choice as an ADR | 12 | Yes |
| 16 | Account lifecycle | Admin-only suspend, reactivate, quota, forced password reset, soft delete with 30-day grace and fresh-backup check; all audited | 15 | Yes |
| 17 | Self-service account | Password, MFA, recovery via Keycloak's account console; recovery email; bulk invites and resend | 14 | Yes |
| 18 | One look | Avedeus theme for Keycloak; "Sign in with Avedeus" as the only button on mailcow's login | 12 | Yes |

## M7 – Privacy

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 19 | Alias screens | Portal pages for aliases and password-manager tokens | 10, 14 | Yes |
| 20 | Privacy controls | Per-user spam threshold, sender allow/block lists, SOGo remote-content defaults | 14 | Yes |
| 21 | SimpleLogin spike (gated) | Patched arm64 build in a public fork, cloud rehearsal with a mailcow transport map, decision ADR | Owner's go-ahead | No |
| 22 | SimpleLogin adapter (gated) | Keycloak login, alias domain, lifecycle via control plane; native aliases stay | 21 | Yes |

## M8 – Automation

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 23 | Rule engine | JSON rule model compiled to Sieve, validator, round-trip tests, ManageSieve adapter. **Needs E4** ([ADR 0014](adr/0014-mail-rule-owner.md)) | 9 | No |
| 24 | Visual rule editor | Portal editor; existing SOGo scripts imported; SOGo's Sieve and vacation UI off (if E4 = Avedeus) | 23, 14 | Yes |
| 25 | Forwarding and vacation | Verified forwarding destinations with rate limits; vacation responder | 24 | Yes |
| 26 | Notifications | Background jobs; quota warnings, new sign-in, alias created; email and push | 9 | Yes |

## M9 – Operations

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 27 | Metrics stack | Prometheus, Grafana (Keycloak login), mailcow exporter with its own RO key, node_exporter; dashboards and alert rules as code | 11, audit 5 | Yes |
| 28 | Audit viewer | Admin view and export of control-plane, Keycloak admin and mailcow API events | 15 | Yes |
| 29 | Recovery for the new parts | Backup and restore of Keycloak, Postgres and control-plane data; restore drill script; runbooks; mailcow update procedure | 11 | Yes |
| 30 | Delivery and DNS checks | Daily delivery probe, DNS and blocklist checks | audit 5 | Yes |
| 31 | Reconciliation | Detect and report drift between Keycloak, mailcow and control-plane links; safe auto-fixes only | 12 | Yes |

## M10 – Extensions

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 32 | Personal API tokens and MCP | Scoped, IP-limited, short-lived tokens; read-only MCP server; both are callers of the control plane with a subset of the portal's capabilities | 9 | Yes |
| 33 | AI assistant (design first) | Opt-in per user, provider choice, drafts only, nothing sent without approval | 32 | Yes |

**Rule for M10: keep it small.** MCP and tokens are just another caller of
the control plane, with the portal's capabilities and the same audit log.
They never get their own route or credential into mailcow, and M10 adds no
separate AI infrastructure.

## Decisions

| # | Question | Status |
|---|---|---|
| E1 | Identity provider | Settled: Keycloak ([ADR 0006](adr/0006-keycloak-identity-provider.md)) |
| E2 | Alias service | Settled: native first, SimpleLogin optional ([ADR 0005](adr/0005-native-aliases-first.md)) |
| E3 | Alias domain | **Open**, needed by PR 10 ([ADR 0013](adr/0013-alias-domain.md)) |
| E4 | Who owns mail rules | **Open**, needed by PR 23 ([ADR 0014](adr/0014-mail-rule-owner.md)) |
| E5 | Mail-client passwords after Keycloak | **Open**, needed by PR 12 ([ADR 0015](adr/0015-mail-client-passwords.md)) |
| E6 | Admin console location | Settled by constraint 2 ([ADR 0010](adr/0010-no-public-lifecycle-admin-separate.md)) |

Earlier decisions D1–D9 (from the M5–M7 plan) still stand, with these changes:

| # | Decision | Now |
|---|---|---|
| D1 | Alerts go to push and email | Done in code (audit fix 5: ntfy, optionally emailed) |
| D2 | External monitors: UptimeRobot + healthchecks.io | healthchecks.io done in code (audit fix 5); UptimeRobot is configuration at setup |
| D3 | Delivery probe to a dedicated Gmail account | Unchanged, PR 30 |
| D4 | Dashboard inside the invite app | **Changed:** Grafana is in scope (PR 27) plus a summary page in the admin console; nothing ops-related goes into the public sign-up app |
| D5 | Build the broker | Done in code (audit fix 7) |
| D6 | Soft-delete grace: 30 days | Unchanged, PR 16 |
| D7 | Self-service recovery via mailcow's recovery email | **Changed:** Keycloak's reset flow (PR 17) |
| D8 | "Suspend but keep receiving" for abuse holds | Unchanged; mailcow offboarding does this (PR 16) |
| D9 | GitHub Actions for CI | Done (audit fix 3) |

## Where the old M5–M7 items went

| Old item | What it was | New home |
|---|---|---|
| 7.1 CI, 7.2 Dependabot | Tests, lint, build, scanning | Audit fix 3 (done in code). arm64 image build and Trivy still to add, with PR 29 at the latest |
| 5.1 collector, 5.3 alerts, 5.4 external monitor | Health, state-change alerts, uptime | Audit fix 5 (done in code) |
| 6.1 broker, 6.2 switch-over | Narrow API in front of the RW key | Audit fix 7 (done in code); grows into the control plane in PR 9 |
| 5.2 ops dashboard, 5.5 delivery probe, 5.6 DNS checks | Admin health page, daily delivery test | M9: PR 27, PR 30 |
| 6.3 accounts page, 6.4 soft delete, 6.5 history | Admin lifecycle with audit trail | M6: PR 15, PR 16; history becomes the PR 9 audit log |
| 6.6 invitation improvements, 6.7 recovery email | Bulk invites, resend, recovery address | PR 17 |
| 7.3 update procedure, 7.4 security checks, 7.5 DR drills, 7.6 runbooks, 7.7 secret rotation | Routine operations | M9: PR 29 (the owner declined a monthly scheduled security-check routine on 2026-11-15; checks stay manual and documented) |

## Open server checks

Stay open until tested on the Oracle box.

- Audit fixes 6–7: edge network and published ports, no route from the
  sign-up container to Postfix/Redis/internet, broker selfcheck against the
  real API, `sshd -T`, alerts arriving.
- Rspamd `/metrics` reachable on mailcow's internal network (PR 27).
- Keycloak start-up and steady RAM/CPU on two Ampere cores (PR 11; Kanidm
  spike is the fallback).
- Whether offboarding or a Keycloak disable ends live SOGo and IMAP sessions
  (PR 16).
