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

PR 28 (2026-11-15): line 4 moves **in code only**: the audit log can be
searched by account and date and exported; the other systems' logs are
linked, not copied. Not deployed.

PR 26 (2026-11-15): line 4 moves **in code only**: new-sign-in mails
write `notify.signin` and each run `notify.run` to the audit log. The control
plane gains one read-only Keycloak role (`view-events`), named for line 3.
Quota warnings are mailcow's own. Not deployed.

PR 20 (2026-11-15): baseline: no change. The portal names the native privacy
and spam settings (mailcow's Spam filter tab, quarantine, SOGo's remote-image
preference) and links to them; nothing new is configured or built. Not deployed.

PR 18 (2026-11-15): baseline: no change. Keycloak's sign-in pages wear the
Avedeus theme (CSS only) and mailcow's own "show only Single Sign-On" option is
documented as a server step. Not deployed.

PR 17 (2026-11-15): baseline lines unchanged. It closes a hole in line 1's
identity (users could change their own email, which is how mailcow finds
them; now admin-only, checked against a real Keycloak) and adds self-service
recovery and invitation conveniences. Not deployed.
Status as of PR 16 (2026-11-15). PR 16 moves line 4 forward **in code
only**: account lifecycle (suspend, reactivate, quota, password reset,
scheduled deletion) exists, admin only, every change audited with the admin
as actor and shown in the account's history; deletions wait for the grace
period and a newer backup (ADR 0021). Not deployed.
PR 15 (2026-11-15). PR 15 moves lines 1, 3, 4 and 9 forward
**in code only**: the admin console is the third app on the one identity, in
its own container; admin calls to the control plane now carry the admin's
verified Keycloak token (group and one-time code), checked against a real
Keycloak in CI; invitations stay in the sign-up app behind an internal API
(ADR 0020, the owner's choice). Not deployed.
PR 14 (2026-11-15). PR 14 moves lines 1 and 9 forward **in
code only**: the first experience app (the portal) signs in with Keycloak
and is built only against the contract; the control plane takes the user
only from a verified Keycloak token. Not deployed.
PR 34 (2026-11-15). PR 34 moves line 3 forward **in code
only**: the admin message trace reads mailcow with its own read-only key,
and the `admin` caller exists (token + source IP; Keycloak sign-in from PR 15).
PR 12 moved lines 1, 2, 4 and 9 forward
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
| 1 | Adding a new service needs no authentication redesign. | Partly. **PR 9:** a new caller is a token, an address list and a row in capabilities.yaml (enforced, checked in CI). **PR 11:** one browser identity exists as code (realm `avedeus`, groups, clients, `/auth/`); a new app becomes a client in the realm file. **PR 12:** sign-up creates the Keycloak user, then the mailbox (Keycloak-sourced; mail apps use app passwords). Not deployed; mailcow's IdP settings are documented, not applied. **PR 14:** the portal is the second app to use it: a realm client, a caller row and a container, with no authentication code of its own beyond the standard OIDC flow (sign-in tested end to end against a real Keycloak). **PR 15:** the admin console is the third: a realm client with its own two-factor flow, a container and the existing `admin` caller row; no new authentication code. | No | PR 11–12 (Keycloak as the one identity) |
| 2 | Every integration is replaceable. | Partly. **PR 9:** a small adapter boundary (typed operations, health, error kinds, a fake) with mailcow as its one adapter. **PR 12:** a second adapter (Keycloak) with its own fake, also tested against a real Keycloak in CI; setting `IDENTITY_PROVIDER=none` removes it without other changes. Two lines of the account flow still name mailcow's Keycloak specifics (integration contract A9). | No | PR 9 (adapter interface, [integration contract](integration-contract.md)) |
| 3 | No component holds unrestricted credentials it does not need. | Partly. Only the broker holds the mailcow key (audit fix 7); it is IP-locked to `.11`. mailcow keys cannot be scoped, so the broker's key is necessarily full-rights; the broker is the mitigation. **PR 34:** the first read-only mailcow key, used only by the admin message trace (a separate client from the read-write one; a config check refuses the read-write key in its place); the trace's `admin` caller was token + IP until PR 15. **PR 15:** admin calls also need the admin's own Keycloak token (client, group, one-time code), verified by the control plane; the token alone works only from inside the broker container (break-glass, audited). The admin console holds no upstream credential. **PR 11:** in Keycloak only the control plane's client may change users; mailcow's may only read them for its sync; no client allows the password grant (checked in CI against a running Keycloak). | No | Server rehearsal of audit fixes 6–7; PR 9; PR 34 (message trace's own read-only key; PR 27 deferred) |
| 4 | Important operations are audited. | Partly. **PR 9:** every mailbox creation, every refused call and every reconciliation run is written to the control plane's append-only audit log (schema-checked in CI) and backed up. The sign-up app keeps its own event log. **PR 12:** every account creation writes `account.create` with the invitation reference; refusals and failures too. **PR 15:** admin events name the admin (Keycloak `sub`) as actor; a first read-only audit view (`readAudit`) in the admin console; invitation changes are logged in the sign-up app's event log with the admin's id (two logs, ADR 0020). **PR 16:** every lifecycle change (suspend, reactivate, quota, password reset, deletion scheduled or cancelled) writes one event naming the admin, refusals and failures too; the deletion job writes `account.purge` per account and `purge.report` per run; completed backups write `backup.completed`; the console shows each account's history. **PR 26:** each sign-in notification (`notify.signin`) and each run (`notify.run`). **PR 28:** the console filters the log by account and date and exports it (CSV, JSON lines); Keycloak's and mailcow's own logs are linked from it. | No | Server proof of the console (needs Keycloak) |
| 5 | Restores are proven. | Partly. Encrypted off-site backup and full wipe-and-restore passed on x86 on 2026-11-14; backups fail loudly on missing parts (audit fix 1). **PR 11:** Keycloak's database is backed up (`pg_dump`, verified with `pg_restore --list`) once enabled, and `restore.sh identity` restored it into a local stack in testing. | No (ARM and real off-site target untested) | M4 restore test on Oracle; PR 29 for Keycloak, Postgres and control-plane data |
| 6 | Resource limits are set and measured on the target hardware. | Partly. Our two containers have `mem_limit` (256 MB, 128 MB); mailcow has none of ours. **PR 11:** Keycloak 1 GB with a 512 MB heap, Postgres 256 MB; about 670 MB and 60 MB measured on x86 in testing, not on Ampere; budget in [architecture.md](architecture.md#resource-budget-oracle-a1-2-ocpu--12-gb) is estimated. | No | Measure on Oracle during M1; PR 11 (Keycloak heap and limits) |
| 7 | Deploys and upgrades are repeatable. | Partly. `setup.sh`, the setup guide and release-tag checkouts exist; CI builds both images. No written mailcow update procedure yet. | No | M1 on Oracle; PR 29/M9 update procedure and runbooks |
| 8 | Drift is detected and reported. | Partly. `health-check.sh` alerts on state changes (audit fix 5); CI fails when contracts and code drift (PR 8, PR 9). **PR 9:** a daily report-only reconciliation compares control-plane-created mailboxes with mailcow and alerts on drift. No Keycloak links yet. | No | PR 31 (adds Keycloak links), PR 30 (DNS checks) |
| 9 | Applications do not need infrastructure knowledge. | Partly. The sign-up app knows only the control plane's URL and its own token, not mailcow; the contract is the only API it sees (checked in CI since PR 9). **PR 12:** it calls createAccount and never learns whether Keycloak is on, only whether mail apps need an app password. **PR 14:** the portal knows only the control plane's URL, its own token and Keycloak's public endpoints; it links to native pages rather than calling their APIs. **PR 15:** the admin console knows only the control plane's URL, the sign-up app's internal API and Keycloak's public endpoints; it links to mailcow's and Keycloak's admin consoles instead of rebuilding them. | No | PR 14 (portal built only against the contract) |

**Overall: not met.** Nothing is deployed yet, and server setup is on hold.
The order stays: M0–M4 on Oracle, then PR 9–13 (M5) to close lines 1–4, 8
and 9 in code, then prove them on the box before M6 experience work grows.
