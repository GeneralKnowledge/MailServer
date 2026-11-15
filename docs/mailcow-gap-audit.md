# mailcow feature-gap audit

Done 2026-11-15 against mailcow-dockerized commit `84828d9` (5 Nov 2026) and
the [official docs](https://docs.mailcow.email/). Reviewed by the owner the
same day; the outcome is in the [roadmap](roadmap.md) and
[ADR 0016](adr/0016-use-what-exists-first.md).

**Result.** mailcow, SOGo and (from PR 12) Keycloak already cover most of the
user self-service we had planned. Avedeus should make that functionality
accessible, coherent and properly explained, and build only where there is a
demonstrable gap. Two real gaps were found: **delivery diagnostics** (PR 34)
and **account export** (runbook first, PR 29).

## Verdicts

| Verdict | Meaning | What Avedeus does |
|---|---|---|
| Adequate | Works for users today | Link, brand, enable. Build nothing |
| Awkward | Exists but hard to find or raw | Defaults, docs, links |
| Admin-only | Exists, only the admin sees it | A narrow, audited slice via the control plane, if worth it |
| Missing | Not in mailcow | Build only for a demonstrable gap |

## By area

| Area | What exists today | Verdict | Avedeus response |
|---|---|---|---|
| App passwords | User-managed, scoped per protocol (IMAP, SMTP, POP3, DAV, EAS, Sieve); required for clients once TFA is on (`templates/user/AppPasswds.twig`, `inc/functions.app_passwd.inc.php`) | Adequate | Link from `/account` (PR 14) |
| Two-factor | TOTP, WebAuthn/FIDO2, Yubi OTP; admin can force TFA and a password change ([docs](https://docs.mailcow.email/manual-guides/mailcow-UI/u_e-mailcow_ui-tfa/)) | Adequate | Moves to Keycloak with PR 12 |
| Quarantine | User lists, releases, deletes; picks digest frequency and category; admin edits the template | Adequate | Digests on by default (O1) |
| Spam controls | Per-user score thresholds, sender allow/block lists, sub-address tag handling, per-user TLS enforcement (`templates/user/Spamfilter.twig`, `tab-user-settings.twig`) | Adequate | Configuration and links (PR 20) |
| Mail filters | SOGo filter editor, vacation, forwarding (`SOGoSieveScriptsEnabled`, `SOGoVacationEnabled`, `SOGoForwardEnabled`); mailcow shows the active script read-only | Awkward (discoverability) | SOGo owns rules ([ADR 0014](adr/0014-mail-rule-owner.md), accepted); PR 23–25 dropped |
| Login history | "Recent successful connections": time, IP, protocol, clearable | Awkward | Link; new-sign-in alerts (PR 26) |
| Sessions | None in mailcow; Keycloak's account console lists and ends browser sessions | Awkward | Link (PR 14); IMAP/SOGo session end is an open server check |
| Account recovery | Recovery email + forgot-password, only for `authsource = mailcow` ([docs](https://docs.mailcow.email/manual-guides/mailcow-UI/u_e-mailcow_ui-forgot_password/)) | Awkward | Keycloak reset after PR 12 (PR 17) |
| Logs and health | Admin UI and API: Postfix, Dovecot, Rspamd history, SOGo, netfilter, API, ACME, watchdog logs; container status; DNS checks; watchdog emails | Admin-only | PR 27, PR 30 |
| Delivery diagnostics | Raw admin log lines; Redis keeps the last 10,000 lines per log ([docs](https://docs.mailcow.email/post_installation/firststeps-logging/)) | Admin-only, **real gap** | PR 34 message trace (implemented in code; not proven on server) |
| Account export | Import via user sync jobs; SOGo exports single address books and calendars; whole-mailbox export is admin CLI only | Missing, **real gap** | Runbooks in PR 29; no export service until needed |
| Undelete | Deleted mailboxes kept 5 days (`MAILDIR_GC_TIME`); daily SOGo profile backup; Dovecot lazy-expunge self-restore folder exists but is off ([docs](https://docs.mailcow.email/manual-guides/Dovecot/u_e-dovecot-lazy_expunge/)) | Missing (off) | Enable via `extra.conf` (O1), verify on Oracle |

Aliases were left out of this audit at the owner's request (no further alias
work proposed here). For the record, mailcow users already have time-limited
"spam aliases" on their own page.

## Outcome (owner review, 2026-11-15)

| # | Opportunity | Treatment |
|---|---|---|
| O1 | Turn on native features (lazy-expunge, quarantine digests, template defaults, SOGo remote images off, user guide) | Accepted. Verify every default during the Oracle rehearsal rather than assuming it works |
| O2 | Message trace | Accepted as **PR 34**: correlate the whole journey, read-only, admin-only, honest about limits. A user-facing explanation needs its own permissions and privacy decision |
| O3 | Security page by linking | Accepted: PR 14 links to Keycloak and mailcow; PR 26 keeps new-sign-in notifications |
| O4 | Privacy as configuration | Accepted: PR 20 is configuration and discoverability only |
| O5 | SOGo stays the rule owner | Accepted (ADR 0014, later the same day). PR 23–25 dropped |
| O6 | Account export | Accepted: runbooks in PR 29; no export service until there is a demonstrated need |

## Not worth building

- A second filter editor or rule templates (two writers break [ADR 0012](adr/0012-one-sieve-rule-owner.md)).
- Cross-service automation or scheduled workflows (no concrete use case; needs a job runner).
- Tracking-pixel stripping (rewrites bodies in the mail path; SOGo's remote-image default gets most of the benefit).
- A custom security dashboard, or copies of mailcow's user pages.

## Scope reduction (same day)

A follow-up pass by the owner kept the architecture and the five
constraints but cut what we intend to build on top of them: Keycloak needs a
validation gate before deployment, the control plane stays a narrow broker,
monitoring starts with alerts and diagnostics (metrics stack deferred), and
MCP, personal tokens and the AI assistant wait for real demand. See
[ADR 0017](adr/0017-minimum-complete-platform.md) for the rules and the
initial ship target.
