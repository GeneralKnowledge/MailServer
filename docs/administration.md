# Administration

## Where to do what

| Task | Where |
|---|---|
| Create / revoke / extend invitations, see who registered, see failures | `https://mail.example.com/join/admin` |
| Suspend a mailbox | mailcow UI → E-Mail → Configuration → Mailboxes → Edit → *Active: off* |
| Change a password | same page, *Password* |
| Change quota | same page, *Quota* |
| Delete a mailbox | same page, Delete (deletes mail too, can't be undone) |
| Add an alias | E-Mail → Configuration → Aliases |
| Mailbox usage | Mailboxes list (quota column) |
| Spam settings | per-domain / global: Configuration → Rspamd; per-user in SOGo or mailcow user UI |
| System health | mailcow System → Status, and `scripts/health-check.sh` |
| Mail queue | mailcow System → Queue manager |

The invite admin intentionally does **not** manage mailboxes; mailcow does.

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
