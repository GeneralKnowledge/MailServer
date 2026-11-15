# Architecture decision records

Each file records one decision: the context, what was decided, and what it
costs. Format: [0001](0001-record-architecture-decisions.md).

**Status values.** *Accepted*: binding; a PR that departs from it must say so
and justify it. *Open*: the question is recorded with a recommendation but
nobody has decided; no PR may assume an answer. *Superseded by NNNN*: kept for
history.

**Implemented** says whether code follows the decision yet, and **Proven on
Oracle** whether that was checked on the real server. They are tracked
separately on purpose.

| ADR | Decision | Status | Implemented | Proven on Oracle |
|---|---|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions as ADRs | Accepted | Yes (this folder) | n/a |
| [0002](0002-layered-platform.md) | Five-layer platform; mail is the first app; small baseline | Accepted | Partly | No |
| [0003](0003-control-plane-owns-upstream-credentials.md) | Only the control plane holds upstream credentials | Accepted | Yes for mailcow (broker, audit fix 7) | No |
| [0004](0004-sources-of-truth.md) | mailcow owns mailboxes, Keycloak owns identities, control plane stores links only | Accepted | Partly (mailcow side) | No |
| [0005](0005-native-aliases-first.md) | mailcow-native aliases first; SimpleLogin optional and gated (E2) | Accepted | No (PR 10) | No |
| [0006](0006-keycloak-identity-provider.md) | Keycloak is the identity provider (E1) | Accepted | No (PR 11, 12) | No |
| [0007](0007-no-shared-databases.md) | No shared databases | Accepted | Yes so far | No |
| [0008](0008-no-changes-inside-mailcow.md) | No changes inside the mailcow tree | Accepted | Yes | No |
| [0009](0009-integrations-behind-adapters.md) | Every integration behind its own adapter | Accepted | Partly (mailcow client in broker) | No |
| [0010](0010-no-public-lifecycle-admin-separate.md) | No public lifecycle; admin in its own container (E6) | Accepted | Partly (no lifecycle exposed; admin split is PR 15) | No |
| [0011](0011-rejected-projects.md) | Rejected projects and alternatives | Accepted | n/a | n/a |
| [0012](0012-one-sieve-rule-owner.md) | Exactly one tool writes each user's Sieve rules | Accepted | Yes (nothing of ours writes Sieve) | No |
| [0013](0013-alias-domain.md) | Alias domain (E3) | **Open**, needed by PR 10 | No | No |
| [0014](0014-mail-rule-owner.md) | Who owns mail rules (E4) | **Open**, needed by PR 23 | No | No |
| [0015](0015-mail-client-passwords.md) | Mail-client passwords after Keycloak (E5) | **Open**, needed by PR 12 | No | No |
