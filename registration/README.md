# Invite registration app

A small FastAPI app that lets invited people create a mailbox on mailcow.
It never talks to mailcow or holds its API key: it asks the provisioning
broker ([`broker/`](../broker/README.md)) to check an address or create a
mailbox. It keeps its own data (invitations and an event log) in SQLite.

- Public: `/join/` (enter a code), `/join/i/<token>` (invite link),
  `/join/register`, `/join/check` (username availability, needs a valid invite)
- Admin: `/join/admin` (password + TOTP)
- Health: `/join/healthz`
- CLI: `python -m app.cli --help`

Deployment: see [docs/deployment.md](../docs/deployment.md#5-registration-app-m3).
Configuration: [infrastructure/registration/.env.example](../infrastructure/registration/.env.example).

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

The tests run the real broker in-process, with `broker/tests/fake_mailcow.py`
(an in-memory stand-in for mailcow's `/api/v1` endpoints) behind it, and cover the brief's acceptance criteria:
valid/expired/revoked/reused invitations, duplicate and reserved usernames,
failed provisioning not consuming the invitation, admin auth and rate limits.

The fake mimics mailcow's response format but is not mailcow. The real API
must be checked once in M3 (see "Real API check" below).

## Real API check (M3, on the server)

1. Create an invitation, register `m3test1`.
2. Confirm the mailbox in the mailcow UI has the right quota and can log in to SOGo.
3. Try `m3test1` again with another invitation: must say *taken*.
4. Create an alias `m3alias@`, try registering `m3alias`: must say *taken*.
5. Temporarily remove the broker's IP from the API allow-list, register: must
   fail with an `auth` error under *Provisioning failures* and the invitation
   must still be valid.
