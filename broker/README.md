# Provisioning broker

The only component that holds the mailcow API key. The public registration
app asks it to do exactly two things:

| Call | What it does |
|---|---|
| `POST /v1/addresses/check` `{local_part}` | Is `<local_part>@MAIL_DOMAIN` valid and free (no mailbox or alias)? |
| `POST /v1/mailboxes` `{local_part, password, name, quota_mb}` | Create the mailbox. Checks first, and confirms it exists if mailcow's reply is lost. |
| `GET /v1/upstream` | Checks the key and allow-list work (used by `scripts/health-check.sh` via `python -m broker.selfcheck`). |
| `GET /healthz` | Liveness, no auth. |

Every call except `/healthz` needs `Authorization: Bearer <BROKER_TOKEN>` and
must come from `BROKER_ALLOWED_CLIENTS` (the registration app's address).

Whatever the caller sends, the broker enforces the rules itself:

- the domain is fixed (`MAIL_DOMAIN`); a caller can't choose another;
- username format and reserved names (`postmaster`, `abuse`, `admin`, …);
- quota between 1 and `MAX_QUOTA_MB`;
- at most `MAX_CREATES_PER_DAY` new mailboxes in 24 hours (failures don't count).

Error replies are JSON `{"ok": false, "kind": …, "detail": …}`. The kinds are:

- `invalid` (422)
- `password` (422)
- `taken` (409)
- `limit` (429)
- `forbidden`/`unauthorized` (403/401)
- `auth`, `unavailable` or `rejected` (502, from mailcow)

Nothing else is offered. Changing or deleting mailboxes, passwords, aliases,
forwards, admins, domains and settings can't be done through the broker, so
a compromised registration app can't do them either. Future admin-only
lifecycle calls (suspend, quota, password reset, scheduled deletion) belong
here, behind a separate admin credential that the public app never sees.

## Running

It is the `broker` service in
[infrastructure/registration/docker-compose.yml](../infrastructure/registration/docker-compose.yml),
configured by `broker.env` ([example](../infrastructure/registration/broker.env.example)).
It is on the internal `avedeus-edge` network at `172.30.240.11` and publishes
no port. mailcow's `API_ALLOW_FROM` must list only that address.

## Development

```sh
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

The tests use `tests/fake_mailcow.py`. The registration app's tests also
run this broker in-process, so they check both sides of the contract.
Against the real mailcow API it still needs the server rehearsal.
