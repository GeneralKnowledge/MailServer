# Administration

## Where to do what

| Task | Where |
|---|---|
| Create / revoke / extend invitations, see who registered, see failures | Admin console `https://<mail host>/console/` (with Keycloak, PR 15); until then `python -m app.cli` in the sign-up container ([admin-console.md](admin-console.md)) |
| List accounts, read the audit log | Admin console (PR 15, read-only) |
| Suspend a mailbox | mailcow UI → E-Mail → Configuration → Mailboxes → Edit → *Active: off* |
| Change a password | same page, *Password* |
| Change quota | same page, *Quota* |
| Delete a mailbox | same page, Delete (deletes mail too, can't be undone) |
| Add an alias | E-Mail → Configuration → Aliases |
| Mailbox usage | Mailboxes list (quota column) |
| Spam settings | per-domain / global: Configuration → Rspamd; per-user in SOGo or mailcow user UI |
| System health | mailcow System → Status, and `scripts/health-check.sh` |
| Mail queue | mailcow System → Queue manager |
| Why didn't a message arrive? | Message trace (below; PR 34) |

The admin console intentionally does **not** manage mailboxes yet (PR 16); mailcow does.

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
