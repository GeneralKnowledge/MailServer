# Architecture decision records

Each file records one decision: the context, what was decided, and what it
costs. Format: [0001](0001-record-architecture-decisions.md).

**Status values.** *Accepted*: binding; a PR that departs from it must say so
and justify it. *Open*: the question is recorded with a recommendation but
nobody has decided; no PR may assume an answer. *Proposed*: an answer is
recommended and favoured but not yet approved by the owner; it binds nothing
until it is Accepted. *Superseded by NNNN*: kept for
history.

**Implemented** says whether code follows the decision yet, and **Proven on
Oracle** whether that was checked on the real server. They are tracked
separately on purpose.

| ADR | Decision | Status | Implemented | Proven on Oracle |
|---|---|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions as ADRs | Accepted | Yes (this folder) | n/a |
| [0002](0002-layered-platform.md) | Five-layer platform; mail is the first app; small baseline | Accepted | Partly | No |
| [0003](0003-control-plane-owns-upstream-credentials.md) | Only the control plane holds upstream credentials | Accepted | Yes for mailcow (broker, audit fix 7; per-caller credentials PR 9) | No |
| [0004](0004-sources-of-truth.md) | mailcow owns mailboxes, Keycloak owns identities, control plane stores links only | Accepted | Partly (mailcow side; PR 12: identity links in the control plane's DB, Keycloak adapter in code) | No |
| [0005](0005-native-aliases-first.md) | mailcow-native aliases first; SimpleLogin optional and gated (E2) | Accepted; alias work **parked** 2026-11-15 | No | No |
| [0006](0006-keycloak-identity-provider.md) | Keycloak is the identity provider (E1); validation gate before deployment | Accepted | Partly (PR 11: stack and realm as code, CI start-and-import test; PR 12: Keycloak adapter and createAccount, mailcow settings documented; not deployed, gate open) | No |
| [0007](0007-no-shared-databases.md) | No shared databases | Accepted | Yes so far | No |
| [0008](0008-no-changes-inside-mailcow.md) | No changes inside the mailcow tree | Accepted | Yes | No |
| [0009](0009-integrations-behind-adapters.md) | Every integration behind its own adapter | Accepted | Partly (adapter boundary and mailcow adapter, PR 9) | No |
| [0010](0010-no-public-lifecycle-admin-separate.md) | No public lifecycle; admin in its own container (E6) | Accepted (path `/console`, see 0020) | Yes in code (no lifecycle exposed; admin console split out in PR 15) | No |
| [0011](0011-rejected-projects.md) | Rejected projects and alternatives | Accepted | n/a | n/a |
| [0012](0012-one-sieve-rule-owner.md) | Exactly one tool writes each user's Sieve rules | Accepted | Yes (nothing of ours writes Sieve) | No |
| [0013](0013-alias-domain.md) | Alias domain (E3) | Closed as non-blocking (aliases parked) | No | No |
| [0014](0014-mail-rule-owner.md) | Who owns mail rules (E4): SOGo owns them | Accepted; PR 23–25 dropped | Yes (nothing of ours writes Sieve) | No |
| [0015](0015-mail-client-passwords.md) | Mail-client passwords after Keycloak (E5): app passwords only | Accepted | Yes in code (PR 12: no password grant, mailboxes sourced from Keycloak, sign-up page explains app passwords; Mailpassword Flow off is a server setting) | No |
| [0016](0016-use-what-exists-first.md) | Use what mailcow, SOGo and Keycloak provide first; build only for a demonstrable gap | Accepted | n/a (review rule) | n/a |
| [0017](0017-minimum-complete-platform.md) | Minimum complete platform; replaceable boundaries without replacement machinery; the removal test | Accepted | n/a (scope rule) | n/a |
| [0018](0018-initial-mail-domain.md) | Initial mail domain is a subdomain (`mail.avedeus.ovh`); root domain untouched | Accepted; host name open point | No | No |
| [0019](0019-identity-network-placement.md) | Keycloak on `avedeus-edge` (`.12`); its own Postgres on an internal network; experience network split deferred to PR 14/15 | Accepted | Yes in code (PR 11) | No |
| [0020](0020-admin-console-and-invitations.md) | Admin console at `/console` with Keycloak admin group + one-time code; invitations stay in the sign-up app behind an internal API; control plane verifies the admin's token | Accepted (owner's choice) | Yes in code (PR 15) | No |
| [0021](0021-account-lifecycle.md) | Account lifecycle: suspend keeps mail arriving (app passwords blocked, not revoked); deletion after 30 days and only after a newer backup | Accepted | Yes in code (PR 16) | No |
