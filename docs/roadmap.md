# Roadmap: M5–M10

Adopted 2026-11-15 as the working plan (source: the "Avedeus integration
platform: research and plan" document). Replaces `docs/roadmap-m5-m7.md`;
nothing from that plan is dropped (see [mapping](#where-the-old-m5m7-items-went)).

**Order.** M0–M4 on the real Oracle server first. **Approved 2026-11-15**
as a staged setup, not permission to skip validation: verify mailcow's native
defaults and supported overrides; validate Keycloak authentication, lifecycle
and resource use (pause its integration if it fails the gate); test app
passwords with real mail clients; measure Postfix/Rspamd log retention for
PR 34; test backups and restoration; verify network isolation and
provisioning; check ARM64 compatibility and memory limits. Audit fixes 1–7 are done in code (PR #1). PR 8 (this
architecture) comes next, then PR 9 onward. Every PR:

- is small enough to review in one sitting, with tests against fakes;
- lands behind a feature flag where it touches users;
- states **implemented in code** and **proven on Oracle** separately;
- updates [platform-baseline.md](platform-baseline.md) when it moves a line
  (code and Oracle proof recorded separately), or says "baseline: no change";
- names and justifies any departure from an accepted ADR or a rejected
  project ([ADR 0011](adr/0011-rejected-projects.md));
- for a new feature, says what mailcow, SOGo or Keycloak already provide and
  why enabling, configuring, linking or explaining it is not enough
  ([ADR 0016](adr/0016-use-what-exists-first.md)).

## Initial ship target

**Avedeus Mail, minimum complete platform** ([ADR 0017](adr/0017-minimum-complete-platform.md)):

1. Reliable mail infrastructure: mailcow, SOGo, DNS, spam filtering, backups
   and tested restoration (M0–M4, PR 29).
2. Safe identity: Keycloak integration after its validation gate, account
   lifecycle, credential separation (PR 11, 12, 15, 16, 17).
3. A small control plane: provisioning, permissions, audit and
   reconciliation (PR 9, 13, 31).
4. A useful portal: one place for account settings, security links,
   administration and existing mailcow functionality (PR 14, 18, 20).
5. Operational intelligence: alerts, delivery diagnostics and recovery tools
   (audit fix 5, PR 28, 30, 34, 29).

Everything else earns its place through demonstrated demand. Before a
component goes into a deployment, ask: **can we remove it without losing a
feature users actually need today?** If yes, it does not belong in the
initial deployment. Make the boundaries replaceable, but don't build the
replacement mechanism until it is needed.

Deferred outside the initial commitment: cross-service automation, a custom
security dashboard, a user-facing message trace, an account export
application, the metrics stack (PR 27), personal API tokens and MCP (PR 32),
the AI assistant (PR 33). Alias work is parked and the rule engine dropped
(see [decision record](#decision-record-2026-11-15)).

**Gap audit (2026-11-15).** The [mailcow feature-gap audit](mailcow-gap-audit.md)
re-scoped PR 14, 20, 23–25 and 29 and added PR 34. Numbering is unchanged:
PR numbers are identifiers, not a strict build order.

**Native defaults on Oracle (audit O1).** During M1 setup and the Oracle
rehearsal, turn on and verify the native features we rely on rather than
assuming they work: lazy-expunge self-restore folder, quarantine digests on
by default, mailbox template defaults (app passwords and recovery email
allowed), SOGo remote images off by default. A short user guide points at
mailcow's user page and SOGo's filters. This is configuration and docs, not a
PR of its own.

"Server: yes" means the PR is not done until it has run on the Oracle box.

## M5 – Integration foundation

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 8 | Architecture and service contracts | Docs and contracts only: [architecture](architecture.md), this roadmap, [ADRs](adr/), [control-plane contract](../contracts/control-plane.openapi.yaml), [capabilities](../contracts/capabilities.yaml), [audit schema](../contracts/audit-event.schema.json), [integration contract](integration-contract.md), [baseline checklist](platform-baseline.md), CI contract check | Audit 7 | No |
| 9 | Control plane core | Broker becomes a **narrow** control plane (no workflow engine, event bus, plugin framework or speculative adapters; [ADR 0017](adr/0017-minimum-complete-platform.md)): adapter interface, mailcow adapter, per-caller credentials checked against capabilities.yaml, append-only audit log, idempotency keys, error envelope for validation errors, nightly reconciliation stub. **Implemented in code** (per-caller tokens, `/v1/health`, report-only daily reconciliation, CI conformance tests); not proven on Oracle | 8 | No |
| 11 | Keycloak and Postgres | **Validation gate first** ([ADR 0006](adr/0006-keycloak-identity-provider.md#validation-gate-before-deployment-added-2026-11-15)): not deployed on Oracle until unified identity is needed today and it fits the box. Then arm64 images, memory caps, capped JVM heap; realm, clients, groups as code; `/auth/` through mailcow nginx; admin console IP-restricted; in backups. **Implemented in code** ([identity.md](identity.md), [ADR 0019](adr/0019-identity-network-placement.md), CI start-and-import test); not deployed, gate open; not proven on Oracle | 8 | Yes |
| 12 | One identity | mailcow IdP set to Keycloak with sync and offboarding; mailbox templates; migration of existing mailboxes; Keycloak adapter; invitation creates the Keycloak user, then the mailbox. Mail clients use **app passwords only**; Mailpassword Flow not enabled ([ADR 0015](adr/0015-mail-client-passwords.md)). **Implemented in code** behind `IDENTITY_PROVIDER=keycloak` (createAccount, Keycloak adapter checked against a real Keycloak in CI, mailcow settings and migration runbook in [identity.md](identity.md#one-identity-pr-12)); not proven on Oracle | 9, 11 | Yes |
| 13 | Health contract | `/v1/health` itself exists since PR 9; PR 13 wires `health-check.sh` and the audit-5 alerts to it with the monitoring caller, one alert per component. **Implemented in code**; not proven on Oracle | 9 | No |

## M6 – User experience

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 14 | Portal shell and links | `/account`, Keycloak sign-in; quota; a Security panel and other entries that **link** to native functionality instead of rebuilding it: Keycloak account console (sessions, MFA, password), mailcow's user page (app passwords, login history, spam settings, quarantine, Apple/CalDAV/CardDAV setup profiles), SOGo (mail, filters, vacation). Onboarding makes creating an app password the first step for mail apps. **Implemented in code** ([portal.md](portal.md); sign-in checked end to end against a real Keycloak in CI); not deployed, not proven on Oracle | 12 | Yes |
| 15 | Admin console split | Admin pages leave the sign-up app for their own container at `/console` (mailcow keeps `/admin`); Keycloak `admins` group + one-time code at every sign-in + IP allow-list; read-only accounts list and audit view; control plane verifies the admin's token. Invitations stay in the sign-up app behind an internal API ([ADR 0020](adr/0020-admin-console-and-invitations.md), owner's choice). **Implemented in code** ([admin-console.md](admin-console.md); two-factor sign-in checked against a real Keycloak in CI); not deployed, not proven on Oracle | 12 | Yes |
| 16 | Account lifecycle | Admin-only suspend, reactivate, quota, forced password reset, soft delete with 30-day grace and fresh-backup check; all audited. Suspend keeps mail arriving (mailcow "disallow login"); app passwords blocked, not revoked ([ADR 0021](adr/0021-account-lifecycle.md)). **Implemented in code** (Keycloak calls checked against a real Keycloak in CI); not deployed, not proven on Oracle | 15 | Yes |
| 17 | Self-service account | Password, MFA, recovery via Keycloak's account console; recovery email; bulk invites and resend. Keycloak's "Forgot password" mails the mailbox through mailcow's port 587; admin-assisted reset in Keycloak's console otherwise; users can't change their own email (mailcow finds them by it). **Implemented in code**; mail delivery not proven, not deployed | 14 | Yes |
| 18 | One look | Avedeus theme for Keycloak's sign-in, recovery and one-time-code pages (CSS only, parent `keycloak.v2`); mailcow's own "show only Single Sign-On" option as the only user login (server step). Button label stays mailcow's "Single Sign-On" (no changes inside mailcow). **Implemented in code**; not proven on server, not deployed | 12 | Yes |

## M7 – Privacy

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 20 | Privacy configuration and discoverability | Configuration and discoverability only: SOGo remote-content defaults and portal links to mailcow's existing per-user spam thresholds and sender allow/block lists. No new privacy UI or control-plane operations. Remote images: SOGo's own default is already `never` and mailcow doesn't change it, so no setting is added; the portal explains it and names the preference. Mailbox template ACL keeps spam and quarantine settings on. **Implemented in code**; not proven on server, not deployed | 14 | Yes |

## M8 – Automation

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 26 | Notifications | Retained. Background jobs; new-sign-in notifications (mailcow has none), quota warnings; email and push | 9 | Yes |

PR 23–25 (rule engine, visual editor, forwarding and vacation) were **dropped**
on 2026-11-15: SOGo owns user-facing rules ([ADR 0014](adr/0014-mail-rule-owner.md)).

## M9 – Operations

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 27 | ~~Metrics stack~~ **Deferred until justified** | Prometheus, Grafana, exporters and dashboards wait for an operational need. Monitoring starts from mailcow's own (watchdog, UI, logs), our alerts (audit fix 5), backup verification, the delivery probe (PR 30) and the message trace (PR 34) | — | — |
| 28 | Audit viewer | Admin view and export of control-plane, Keycloak admin and mailcow API events | 15 | Yes |
| 29 | Recovery for the new parts | Backup and restore of Keycloak, Postgres and control-plane data; restore drill script; runbooks; mailcow update procedure; **account export and recovery runbooks** (one account's mail via Dovecot export, contacts, calendars and filters via `sogo-tool`; undelete via lazy-expunge and `MAILDIR_GC_TIME`). No export service until there is a demonstrated need | 11 | Yes |
| 30 | Delivery and DNS checks | Daily delivery probe, DNS and blocklist checks | audit 5 | Yes |
| 31 | Reconciliation | A simple scheduled job that detects and reports drift between Keycloak, mailcow and control-plane links into the one audit trail; safe auto-fixes only | 12 | Yes |
| 34 | Message trace (admin) | Read-only, admin-only trace that **correlates a message's whole journey** across mailcow's Postfix log, Rspamd history, quarantine and queue into one plain explanation. Details below. **Implemented in code** (`GET /v1/trace`, `python -m broker.trace`; [administration.md](administration.md#message-trace-pr-34)); not proven on Oracle | 9, 15; its own read-only mailcow key | Yes |

### PR 34: message trace

The admin gives an address, a time window or a Message-ID. The trace follows
the message by queue ID and Message-ID across sources, and reports each stage
it can establish:

| Stage | Established from |
|---|---|
| Never reached our server | No Postfix connection or queue entry matches in the retained window |
| Rejected during SMTP | Postfix reject line (and the reason: policy, RBL, Rspamd reject, unknown recipient) |
| Accepted by Postfix | Postfix queue ID assigned |
| Classified as spam or quarantined | Rspamd history (score, action, main symbols); quarantine entry |
| Deferred awaiting delivery | Queue entry or `status=deferred` with the remote reply |
| Accepted by the destination server | Outbound `status=sent` with the remote server's reply |
| Delivered locally to a mailbox | Local delivery to Dovecot (`status=sent` via LMTP), plus the target folder where known |

Rules:

- **Read-only and admin-only.** It changes nothing (no release, resend or
  queue action) and is reachable only from the admin console. It reads mailcow
  through the control plane with its own read-only key (the first one, since
  PR 27 is deferred), and every trace is
  written to the audit log.
- **Honest about limits.** Each answer says what the trace cannot establish:
  "accepted by the destination server" does not prove the recipient saw it;
  "delivered locally" does not prove it was read; "never reached us" only
  covers the retained log window; mail that never connected to our server
  leaves no trace here at all.
- **No user-facing version here.** A future user-facing delivery explanation
  is a separate feature that needs its own permissions and privacy decision
  (an ADR) first; PR 34 does not create it.
- The look-back is limited by mailcow's Redis log window (10,000 lines per
  log); measuring how many days that covers is an open server check. Longer
  retention would be a separate decision, not part of PR 34.
- Adds a planned `traceMessage` operation to the contract, granted to `admin`
  only.

## M10 – Extensions

| PR | Title | Delivers | Depends on | Server |
|---|---|---|---|---|
| 32 | ~~Personal API tokens and MCP~~ **Deferred until an actual integration needs it** | Scoped, IP-limited, short-lived tokens; read-only MCP server; both are callers of the control plane with a subset of the portal's capabilities | 9 | Yes |
| 33 | ~~AI assistant~~ **Deferred** (design first, if ever) | Opt-in per user, provider choice, drafts only, nothing sent without approval | 32 | Yes |

**Rule for M10: keep it small.** MCP and tokens are just another caller of
the control plane, with the portal's capabilities and the same audit log.
They never get their own route or credential into mailcow, and M10 adds no
separate AI infrastructure.

## Decisions

| # | Question | Status |
|---|---|---|
| E1 | Identity provider | Settled: Keycloak ([ADR 0006](adr/0006-keycloak-identity-provider.md)) |
| E2 | Alias service | Settled: native first, SimpleLogin optional ([ADR 0005](adr/0005-native-aliases-first.md)) |
| E3 | Alias domain | Closed as non-blocking while aliases are parked ([ADR 0013](adr/0013-alias-domain.md)) |
| E4 | Who owns mail rules | Settled: SOGo ([ADR 0014](adr/0014-mail-rule-owner.md)) |
| E5 | Mail-client passwords after Keycloak | Settled: protocol-scoped app passwords only ([ADR 0015](adr/0015-mail-client-passwords.md)) |
| E6 | Admin console location | Settled by constraint 2 ([ADR 0010](adr/0010-no-public-lifecycle-admin-separate.md)) |
| E7 | Initial mail domain | Settled: a subdomain, `mail.avedeus.ovh`; root domain untouched; server host name still to confirm ([ADR 0018](adr/0018-initial-mail-domain.md)) |

### Decision record (2026-11-15)

| Decision | Outcome |
|---|---|
| E4 | SOGo owns rules |
| PR 23–25 | Dropped |
| Aliases PR 10, 19, 21, 22 | Parked: possible future work, not planned deliverables |
| E3 | Non-blocking |
| E5 | Protocol-scoped app passwords |
| Initial mail domain | `mail.avedeus.ovh` (root `avedeus.ovh` untouched; moving to the root is a later decision) |
| Oracle setup | Approved for M0–M4, with the validation list under **Order** |
| PR 34 | Retained, pending the log-window measurement |
| PR 1 | Already merged |

### Possible future work (not planned)

Native mailcow aliases remain available to users and admins. These are kept
only so the thinking is not lost; none is a planned deliverable, and reviving
one needs the owner's go-ahead and a removal-test answer
([ADR 0017](adr/0017-minimum-complete-platform.md)).

| Former PR | Was | Notes |
|---|---|---|
| 10 | Alias back end (mailcow-native) through the control plane, with SimpleLogin/addy-compatible endpoints | Direction still set by [ADR 0005](adr/0005-native-aliases-first.md); E3 reopens if revived. The contract keeps its operations as a sketch |
| 19 | Portal alias screens and password-manager tokens | Depends on 10 |
| 21 | SimpleLogin spike (gated) | amd64-only upstream images |
| 22 | SimpleLogin adapter (gated) | Depends on 21 |

Earlier decisions D1–D9 (from the M5–M7 plan) still stand, with these changes:

| # | Decision | Now |
|---|---|---|
| D1 | Alerts go to push and email | Done in code (audit fix 5: ntfy, optionally emailed) |
| D2 | External monitors: UptimeRobot + healthchecks.io | healthchecks.io done in code (audit fix 5); UptimeRobot is configuration at setup |
| D3 | Delivery probe to a dedicated Gmail account | Unchanged, PR 30 |
| D4 | Dashboard inside the invite app | **Changed:** a summary page in the admin console; Grafana (PR 27) deferred until justified; nothing ops-related goes into the public sign-up app |
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
| 5.2 ops dashboard, 5.5 delivery probe, 5.6 DNS checks | Admin health page, daily delivery test | M9: PR 30, admin summary page; PR 27 deferred |
| 6.3 accounts page, 6.4 soft delete, 6.5 history | Admin lifecycle with audit trail | M6: PR 15, PR 16; history becomes the PR 9 audit log |
| 6.6 invitation improvements, 6.7 recovery email | Bulk invites, resend, recovery address | PR 17 |
| 7.3 update procedure, 7.4 security checks, 7.5 DR drills, 7.6 runbooks, 7.7 secret rotation | Routine operations | M9: PR 29 (the owner declined a monthly scheduled security-check routine on 2026-11-15; checks stay manual and documented) |

## Open server checks

Stay open until tested on the Oracle box.

- Audit fixes 6–7: edge network and published ports, no route from the
  sign-up container to Postfix/Redis/internet, broker health probe against the
  real API, `sshd -T`, alerts arriving.
- Rspamd `/metrics` reachable on mailcow's internal network (PR 27, deferred; check only if it is revived).
- Keycloak start-up and steady RAM/CPU on two Ampere cores (PR 11; Kanidm
  spike is the fallback).
- Whether offboarding or a Keycloak disable ends live SOGo and IMAP sessions
  (PR 16; also affects PR 14's Security panel). Also whether mailcow's
  identity-provider sync changes a mailbox's state when its Keycloak user is
  disabled (ADR 0021), and the deletion job against real mailcow and backups.
- PR 17: Keycloak's "Forgot password" mail reaching mailcow's port 587 at
  `avedeus-edge`'s gateway (`172.30.240.1`) and being delivered; and, seen in
  local testing, that any container on `avedeus-edge` can reach the host's
  published ports through that gateway. If that holds on the server, audit
  fix 6 ("no route from the sign-up container to Postfix") needs a host
  firewall rule allowing only Keycloak to 587.
- PR 18: mailcow's "show only Single Sign-On" option on the real login page,
  `/admin` still reachable with its own form, and `/SOGo` not offering a
  separate password form.
- PR 20: SOGo shows remote images as blocked by default for a new user
  (`docker compose exec sogo-mailcow sogo-tool dump-defaults` shows no
  override), and an `avedeus-user` mailbox sees the *Spam filter* tab.
- Every native default from audit O1 works on ARM64 and survives a mailcow
  update (lazy-expunge via `data/conf/dovecot/extra.conf`, quarantine digests,
  mailbox template defaults, SOGo remote-content default).
- How many days of Postfix and Rspamd history the 10,000-line Redis window
  holds on our traffic (sets PR 34's look-back).
- App passwords work with real mail clients (phone and desktop: IMAP, SMTP,
  CalDAV/CardDAV), including for IdP-linked mailboxes after PR 12, and stop
  working when a mailbox is offboarded (ADR 0015).
- The final host names for [ADR 0018](adr/0018-initial-mail-domain.md):
  mailcow hostname versus the `mail.avedeus.ovh` mail domain.
