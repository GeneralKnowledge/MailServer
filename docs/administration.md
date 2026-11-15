# Administration

## Where to do what

| Task | Where |
|---|---|
| Create / revoke / extend invitations, see who registered, see failures | Admin console `https://<mail host>/console/` (with Keycloak, PR 15); until then `python -m app.cli` in the sign-up container ([admin-console.md](admin-console.md)) |
| List accounts, read the audit log | Admin console (PR 15) |
| Suspend, reactivate, change quota, ask for a new password, delete an account | Admin console → Accounts → the account (PR 16, audited; [below](#account-lifecycle-pr-16)). Without Keycloak: mailcow UI → E-Mail → Configuration → Mailboxes → Edit |
| Set a password for an account without a Keycloak identity | mailcow UI, the mailbox's *Password* |
| Delete a mailbox at once (no grace, no backup check) | mailcow UI, Delete. Avoid: the console's scheduled deletion keeps a backup and an audit trail |
| Add an alias | E-Mail → Configuration → Aliases |
| Mailbox usage | Mailboxes list (quota column) |
| Spam settings | per-domain / global: Configuration → Rspamd; per-user in SOGo or mailcow user UI |
| System health | mailcow System → Status, and `scripts/health-check.sh` |
| Mail queue | mailcow System → Queue manager |
| Why didn't a message arrive? | Message trace (below; PR 34) |

Aliases, app passwords and every other mailbox setting stay in mailcow's admin; users,
groups and sessions in Keycloak's.

## Account lifecycle (PR 16)

> **Implemented in code; not proven on server; not deployed** (the console
> needs Keycloak, ADR 0006 gate). Decision record: [ADR 0021](adr/0021-account-lifecycle.md).

On an account's page in the admin console:

| Button | What happens |
|---|---|
| Suspend | Keycloak user disabled and signed out; mailcow "disallow login": nobody can sign in to webmail, mail apps (app passwords too) or the account page. **Mail keeps arriving** unless "also stop new mail" is ticked. |
| Reactivate | Sign-in works again, app passwords included. |
| Set quota | 1 MB up to `MAX_QUOTA_MB`, never below what the mailbox already uses. |
| Ask for a new password | Keycloak asks at the next sign-in; open sessions end. Mail apps keep working (app passwords); suspend to stop them. |
| Schedule deletion | Type the address to confirm. Suspended now (mail still arrives); deleted with all its mail after 30 days (`DELETION_GRACE_DAYS`), and only once a backup that started after you scheduled it has completed. |
| Cancel the deletion | Back to how it was before (a suspended account stays suspended). |

Every change is one audit event naming you, shown in the account's history.
The deletion itself is done by the daily job, which alerts if a deletion fails
or was stopped (for example because someone reactivated the mailbox in
mailcow's admin):

```sh
sudo /opt/MailServer/scripts/purge-deleted.sh --dry-run   # what would be deleted now
```

Without the console (break-glass, from inside the broker container; audited
as `admin-break-glass`, same checks as the console):

```sh
cd /opt/MailServer/infrastructure/registration
sudo docker compose exec broker python -m broker.account show sam@mail.avedeus.ovh
sudo docker compose exec broker python -m broker.account suspend sam@mail.avedeus.ovh   # --stop-receiving to refuse mail too
sudo docker compose exec broker python -m broker.account quota sam@mail.avedeus.ovh 4096
sudo docker compose exec broker python -m broker.account delete sam@mail.avedeus.ovh    # schedules; cancel-delete undoes
```

## Message trace (PR 34)

> Implemented in code; not proven on server. The log shapes it parses come
> from mailcow's API and Postfix, Rspamd and Dovecot's formats; checking them
> on the real server is an open item.

Read-only and admin-only. On the server:

```sh
cd /opt/MailServer/infrastructure/registration
docker compose exec -T broker python -m broker.trace --address someone@example.com
docker compose exec -T broker python -m broker.trace --message-id '<id@example.net>' --hours 72
docker compose exec -T broker python -m broker.trace --hours 2          # everything in the last 2 hours
```

It follows each message by Postfix queue ID and Message-ID across mailcow's
Postfix log, Rspamd history, quarantine, mail queue and Dovecot delivery log,
and prints one plain line per stage: rejected during SMTP (blocklist,
unknown recipient, spam filter, policy), accepted, spam check (action, score,
main symbols), quarantined, deferred (with the remote reply), accepted by the
destination server, delivered to a mailbox (with the folder where known),
bounced, still queued; or that nothing matched in the retained log. Add
`--json` for the raw answer. Exit code 0 = something found, 1 = nothing.

What it cannot tell you is printed with every answer: it only searches the
retained log window (the newest `TRACE_LOG_LINES` lines of each mailcow log,
10,000 by default; how many days that covers is an open server check), mail
that never connected leaves no trace, "accepted by the destination server"
doesn't prove the recipient saw it, and "delivered" doesn't prove it was read.

It changes nothing: no release, resend or queue action (use mailcow's
quarantine and queue pages for those). It never shows subjects or content.
Every trace is written to the control plane's audit log with the address
searched and counts only.

Setup (once):

1. mailcow UI → System → Configuration → Access → API → **Read-only access**:
   generate the key, allow it from `172.30.240.11` only (the control plane),
   activate it.
2. In `broker.env`: `MAILCOW_READ_ONLY_API_KEY=<that key>`, and
   `ADMIN_TOKEN=$(openssl rand -hex 32)` with `ADMIN_CLIENTS=127.0.0.1`
   (the CLI runs inside the broker container). Restart the broker.

## Invitations

- Default: single use, 7 days, 2 GB. Quota and expiry can be changed per
  invitation when creating it.
- The link/code is shown **once** when created. If it's lost, revoke and
  create another.
- *Extend* sets expiry to 7 days from now; it also renews an expired, unused
  invitation.
- *Revoke* works until the invitation is used.
- A failed registration (shown under *Provisioning failures*) does not use the
  invitation; the user can try again.

Command line alternative (on the server):

```sh
cd /opt/MailServer/infrastructure/registration
sudo docker compose exec registration python -m app.cli create-invite --note "Alice" --quota 2048 --days 7
sudo docker compose exec registration python -m app.cli list
sudo docker compose exec registration python -m app.cli revoke 12
```

## Mailcow domain quota

The domain's total quota and max mailbox count (Configuration → Domains)
must leave room for new mailboxes, or registrations fail with a
`quota` error in *Provisioning failures*.
