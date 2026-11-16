# 0021. Account lifecycle: suspend keeps mail, deletion waits for a newer backup

- Status: **Accepted** (2026-11-15, PR 16). Follows the roadmap decisions D6
  (30-day grace) and D8 (suspend but keep receiving); no new owner choice.
- Implemented: Yes in code (`broker/broker/main.py` lifecycle routes,
  `broker/broker/purge.py`, `broker/broker/backup.py --mark-complete`,
  `scripts/purge-deleted.sh`, the console's account page; the Keycloak
  calls checked against a real Keycloak in CI). Proven on Oracle: No.

## Context

[ADR 0010](0010-no-public-lifecycle-admin-separate.md) keeps account
lifecycle out of the public app: admin only, audited. mailcow's admin can
already suspend and delete a mailbox, and Keycloak's console can disable a
user, but with one identity (PR 12) an account is both, and nothing ties the
two together, records who did it, or stops a deletion from destroying the
only copy of someone's mail. That is the gap PR 16 fills
([ADR 0016](0016-use-what-exists-first.md)); everything else about a mailbox
(aliases, app passwords, spam settings) stays in mailcow's admin.

## Decision

1. **The control plane owns the lifecycle operations**, admin only, each
   with an Idempotency-Key and exactly one audit event naming the admin:
   `getAccount`, `suspendAccount`, `reactivateAccount`, `setAccountQuota`,
   `forcePasswordReset`, `scheduleDeletion`, `cancelDeletion`. The account
   is addressed by its mailbox address on the mail domain.
2. **Suspend** disables the Keycloak user and ends its sessions first (the
   browser sign-ins stop at once), then sets mailcow's mailbox to
   **"disallow login"** (`active=2`): mail keeps arriving (D8), and mailcow
   refuses the password, the app passwords and the webmail sign-in.
   `stop_receiving` sets the mailbox fully off (`active=0`) instead.
   **Named deviation:** the earlier contract said suspend *revokes* app
   passwords. mailcow already refuses every app password of a mailbox that
   isn't `active=1` (checked in mailcow's `functions.auth.inc.php`), so
   they are blocked, not deleted, and reactivating restores them without
   every mail app being set up again. To revoke them for good, delete them
   in mailcow's admin.
3. **Reactivate** sets mailcow back to `active=1` and enables the Keycloak
   user. It is refused while a deletion is scheduled (cancel that instead).
4. **Quota** is set in mailcow, bounded by `MAX_QUOTA_MB` and never below
   what the mailbox already uses.
5. **Forced password reset** adds Keycloak's `UPDATE_PASSWORD` required
   action and ends the user's sessions. Without a linked identity it is
   refused with a pointer to mailcow's admin. Mail apps use app passwords
   ([ADR 0015](0015-mail-client-passwords.md)) and are not affected; suspend
   to stop them.
6. **Deletion is two steps.** `scheduleDeletion` suspends the account (mail
   still arrives, so a cancellation loses nothing) and records it in the
   control plane's own database with a due time `DELETION_GRACE_DAYS` (30,
   D6) ahead. The **deletion job** (`python -m broker.purge`, daily from
   `scripts/purge-deleted.sh`) deletes the mailbox and then the Keycloak user
   only when:
   - the due time has passed;
   - a backup that **started after** the deletion was scheduled has
     completed (`scripts/backup.sh` records each successful, verified,
     uploaded run with `python -m broker.backup --mark-complete <start>`);
   - the mailbox is still suspended in mailcow (reactivated in mailcow's own
     admin means stopped and reported, not deleted).

   `cancelDeletion` returns the account to the state it had before
   (a suspended account stays suspended). Each deletion writes
   `account.purge`; each run writes `purge.report`; failures alert.
7. **No new public surface.** The job runs inside the broker container; it
   has no HTTP route. The console's account page calls the operations with
   the admin's own token and a fresh Idempotency-Key per page, and asks for
   the address to be typed before scheduling a deletion.

## Consequences

- Everything a lifecycle change does is in one audit log with the admin's
  Keycloak id, and the account page shows its history.
- Without Keycloak (IDENTITY_PROVIDER=none) the same operations act on
  mailcow only and are reachable only by break-glass from inside the broker
  container (ADR 0020); password reset is refused.
- A deletion can wait longer than the grace period if backups fail; that is
  intended (the backup alert already fires).
- **Not proven on a server:** whether mailcow's "disallow login" also ends
  open IMAP and SOGo sessions (roadmap server checklist), and whether
  mailcow's identity-provider sync changes a mailbox's state when its
  Keycloak user is disabled.
- **Removal test ([ADR 0017](0017-minimum-complete-platform.md)):** remove
  the lifecycle routes, the deletion job's cron line and the backup marker,
  and mailcow's and Keycloak's admin consoles still suspend, delete and
  reset as before; the `deletions` table is left unused. Nothing else changes.
