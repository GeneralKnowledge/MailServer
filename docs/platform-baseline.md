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

Status as of PR 8 (2026-11-15).

| # | The baseline is done when… | In code | Proven on Oracle | What closes it |
|---|---|---|---|---|
| 1 | Adding a new service needs no authentication redesign. | No. Only the sign-up app has a caller credential; there is no shared identity. | No | PR 9 (per-caller credentials and capabilities), PR 11–12 (Keycloak as the one identity) |
| 2 | Every integration is replaceable. | Partly. mailcow is reached only through `broker/broker/mailcow.py`, but there is no adapter interface yet. | No | PR 9 (adapter interface, [integration contract](integration-contract.md)) |
| 3 | No component holds unrestricted credentials it does not need. | Partly. Only the broker holds the mailcow key (audit fix 7); it is IP-locked to `.11`. mailcow keys cannot be scoped, so the broker's key is necessarily full-rights; the broker is the mitigation. No read-only monitoring key yet. | No | Server rehearsal of audit fixes 6–7; PR 9; PR 34 (message trace's own read-only key; PR 27 deferred) |
| 4 | Important operations are audited. | Partly. The sign-up app logs invitations, sign-ups and admin logins in its own event log. No control-plane audit log yet. | No | PR 9 (append-only audit log in the [schema](../contracts/audit-event.schema.json)), PR 28 (viewer) |
| 5 | Restores are proven. | Partly. Encrypted off-site backup and full wipe-and-restore passed on x86 on 2026-11-14; backups fail loudly on missing parts (audit fix 1). | No (ARM and real off-site target untested) | M4 restore test on Oracle; PR 29 for Keycloak, Postgres and control-plane data |
| 6 | Resource limits are set and measured on the target hardware. | Partly. Our two containers have `mem_limit` (256 MB, 128 MB); mailcow has none of ours; budget in [architecture.md](architecture.md#resource-budget-oracle-a1-2-ocpu--12-gb) is estimated. | No | Measure on Oracle during M1; PR 11 (Keycloak heap and limits) |
| 7 | Deploys and upgrades are repeatable. | Partly. `setup.sh`, the setup guide and release-tag checkouts exist; CI builds both images. No written mailcow update procedure yet. | No | M1 on Oracle; PR 29/M9 update procedure and runbooks |
| 8 | Drift is detected and reported. | Partly. `health-check.sh` alerts on state changes (audit fix 5), and CI now fails when the contracts and the broker drift (this PR). No Keycloak/mailcow/links reconciliation yet. | No | PR 31 (reconciliation), PR 30 (DNS checks) |
| 9 | Applications do not need infrastructure knowledge. | Partly. The sign-up app knows only the broker's URL and token, not mailcow. | No | PR 9 (control plane as the only API apps see), PR 14 (portal built only against the contract) |

**Overall: not met.** Nothing is deployed yet, and server setup is on hold.
The order stays: M0–M4 on Oracle, then PR 9–13 (M5) to close lines 1–4, 8
and 9 in code, then prove them on the box before M6 experience work grows.
