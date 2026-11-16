# Platform baseline: definition of done

The owner's test (2026-11-15) for when the foundations (layers 1–4 in
[architecture.md](architecture.md#layers)) are dependable enough to invest in
the experience layer. Each line is tracked twice: **in code** and **proven on
Oracle**. A line counts as met only when both say yes.

**This is a living acceptance checklist.** Every PR must update it when it
genuinely moves a line forward, and say so in its description. The two
columns are recorded separately: a PR that finishes the code moves only
**In code**; **Proven on Oracle** moves only when the thing has actually been
shown working on the server, with a note of what was checked and when. A PR
that moves no line says "baseline: no change". This stops work from looking
finished when it has never been shown working together on the real box.

Status as of PR 12 (2026-11-15). PR 12 moves lines 1, 2, 4 and 9 forward
**in code only**: accounts are made identity-first behind
`IDENTITY_PROVIDER=keycloak` (off by default), and the Keycloak adapter is
checked against a real Keycloak in CI, but nothing is deployed.
PR 11 moved lines 1, 3, 5 and 6 forward
**in code only**: Keycloak and its Postgres exist as code and pass a
start-and-import test in CI, but are not deployed (ADR 0006 gate open), so
nothing moves in the server column. PR 13: baseline lines unchanged; it
connects existing alerts to `/v1/health` (operational foundation, no new line).

| # | The baseline is done when… | In code | Proven on Oracle | What closes it |
|---|---|---|---|---|
| 1 | Adding a new service needs no authentication redesign. | Partly. **PR 9:** a new caller is a token, an address list and a row in capabilities.yaml (enforced, checked in CI). **PR 11:** one browser identity exists as code (realm `avedeus`, groups, clients, `/auth/`); a new app becomes a client in the realm file. **PR 12:** sign-up creates the Keycloak user, then the mailbox (Keycloak-sourced; mail apps use app passwords). Not deployed; mailcow's IdP settings are documented, not applied. | No | PR 11–12 (Keycloak as the one identity) |
| 2 | Every integration is replaceable. | Partly. **PR 9:** a small adapter boundary (typed operations, health, error kinds, a fake) with mailcow as its one adapter. **PR 12:** a second adapter (Keycloak) with its own fake, also tested against a real Keycloak in CI; setting `IDENTITY_PROVIDER=none` removes it without other changes. Two lines of the account flow still name mailcow's Keycloak specifics (integration contract A9). | No | PR 9 (adapter interface, [integration contract](integration-contract.md)) |
| 3 | No component holds unrestricted credentials it does not need. | Partly. Only the broker holds the mailcow key (audit fix 7); it is IP-locked to `.11`. mailcow keys cannot be scoped, so the broker's key is necessarily full-rights; the broker is the mitigation. No read-only monitoring key yet. **PR 11:** in Keycloak only the control plane's client may change users; mailcow's may only read them for its sync; no client allows the password grant (checked in CI against a running Keycloak). | No | Server rehearsal of audit fixes 6–7; PR 9; PR 34 (message trace's own read-only key; PR 27 deferred) |
| 4 | Important operations are audited. | Partly. **PR 9:** every mailbox creation, every refused call and every reconciliation run is written to the control plane's append-only audit log (schema-checked in CI) and backed up. The sign-up app keeps its own event log. **PR 12:** every account creation writes `account.create` with the invitation reference; refusals and failures too. Lifecycle operations don't exist yet; they are audited when built. | No | PR 16 (lifecycle), PR 28 (viewer) |
| 5 | Restores are proven. | Partly. Encrypted off-site backup and full wipe-and-restore passed on x86 on 2026-11-14; backups fail loudly on missing parts (audit fix 1). **PR 11:** Keycloak's database is backed up (`pg_dump`, verified with `pg_restore --list`) once enabled, and `restore.sh identity` restored it into a local stack in testing. | No (ARM and real off-site target untested) | M4 restore test on Oracle; PR 29 for Keycloak, Postgres and control-plane data |
| 6 | Resource limits are set and measured on the target hardware. | Partly. Our two containers have `mem_limit` (256 MB, 128 MB); mailcow has none of ours. **PR 11:** Keycloak 1 GB with a 512 MB heap, Postgres 256 MB; about 670 MB and 60 MB measured on x86 in testing, not on Ampere; budget in [architecture.md](architecture.md#resource-budget-oracle-a1-2-ocpu--12-gb) is estimated. | No | Measure on Oracle during M1; PR 11 (Keycloak heap and limits) |
| 7 | Deploys and upgrades are repeatable. | Partly. `setup.sh`, the setup guide and release-tag checkouts exist; CI builds both images. No written mailcow update procedure yet. | No | M1 on Oracle; PR 29/M9 update procedure and runbooks |
| 8 | Drift is detected and reported. | Partly. `health-check.sh` alerts on state changes (audit fix 5); CI fails when contracts and code drift (PR 8, PR 9). **PR 9:** a daily report-only reconciliation compares control-plane-created mailboxes with mailcow and alerts on drift. No Keycloak links yet. | No | PR 31 (adds Keycloak links), PR 30 (DNS checks) |
| 9 | Applications do not need infrastructure knowledge. | Partly. The sign-up app knows only the control plane's URL and its own token, not mailcow; the contract is the only API it sees (checked in CI since PR 9). **PR 12:** it calls createAccount and never learns whether Keycloak is on, only whether mail apps need an app password. | No | PR 14 (portal built only against the contract) |

**Overall: not met.** Nothing is deployed yet, and server setup is on hold.
The order stays: M0–M4 on Oracle, then PR 9–13 (M5) to close lines 1–4, 8
and 9 in code, then prove them on the box before M6 experience work grows.
