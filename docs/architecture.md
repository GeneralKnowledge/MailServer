# Architecture

```
                 Internet
                    │  25 465 587 993 (4190)        80 443
                    ▼                                ▼
┌──────────────── Oracle Cloud VM (Ubuntu 24.04, ARM64) ───────────────────┐
│  OCI security list = primary firewall                                    │
│                                                                          │
│  /opt/mailcow-dockerized  (upstream, untouched except supported files)  │
│   ├ postfix, dovecot, rspamd, clamd, mariadb, redis, sogo, acme, …       │
│   └ nginx-mailcow ── /           → mailcow UI / SOGo (/SOGo)             │
│                    ── /api/v1     → mailcow API (key IP-allow-listed)    │
│                    ── /join/      → registration:8000 (site.*.custom)    │
│                                   ▲  avedeus-edge (internal, 172.30.240/24)│
│  /opt/MailServer/infrastructure/registration (separate compose project)  │
│   ├ registration (FastAPI + SQLite volume), 172.30.240.10: no API key;   │
│   │   asks the broker to check an address or create a mailbox           │
│   └ broker (holds the mailcow key), 172.30.240.11, no published port;    │
│       calls https://nginx-mailcow/api/v1. Neither is on mailcow's network│
└──────────────────────────────────────────────────────────────────────────┘
        backups ──rclone (encrypted)──► off-site storage
```

## Components

| Piece | Source | Where |
|---|---|---|
| Mail server, webmail, admin UI, TLS | mailcow-dockerized (official) | `/opt/mailcow-dockerized` |
| Invite website + invite admin | this repo, `registration/` | `infrastructure/registration/docker-compose.yml` |
| Provisioning broker (sole holder of the mailcow API key) | this repo, `broker/` | same compose project |
| Proxy rule for `/join/` | this repo | copied to `data/conf/nginx/site.registration.custom` |
| Webmail branding | this repo, `branding/` | copied to `data/conf/sogo/custom-*` |
| Backups | mailcow helper + `scripts/backup.sh` | `/var/backups/mailserver/mailcow` → off-site |

All files we add inside the mailcow tree are ones mailcow's `.gitignore`
excludes, so `update.sh` leaves them alone.

## Registration flow

1. Admin creates an invitation at `/join/admin` (or the CLI). The token is
   192-bit random; only its SHA-256 is stored.
2. The user opens `https://mail.<domain>/join/i/<token>`, chooses a username
   and password.
3. The app validates the input and **claims** the invitation (atomic DB
   update), then asks the provisioning broker to create the mailbox. The
   broker re-checks the rules, checks the address isn't a mailbox or alias,
   and calls mailcow's `POST /api/v1/add/mailbox`.
4. Success → invitation marked redeemed. Failure → claim released, failure
   logged for the admin, invitation still usable.
5. The user is sent to SOGo at `https://mail.<domain>`.

The mailbox password goes straight to mailcow and is never stored by the app.

## Why no Caddy

mailcow's nginx already terminates TLS with Let's Encrypt for
`mail.<domain>`. The invite site is a path on the same host, so a second
proxy would only add another certificate manager and port conflicts.

## Networks

| Network | Members | Why |
|---|---|---|
| mailcow's own (`172.22.1.0/24`) | all mailcow containers | Postfix trusts this whole subnet to send mail without a password, so nothing of ours joins it. |
| `avedeus-edge` (`172.30.240.0/24`, internal) | nginx-mailcow `.2`, registration `.10`, broker `.11` | nginx proxies `/join/` here; the broker reaches the mailcow API through nginx (the key is allow-listed to `.11` only). Internal: no route to the internet. Created by `scripts/create-edge-network.sh`; nginx joins it via mailcow's `docker-compose.override.yml`. |

To check on the server (part of the rehearsal): from the registration
container, `postfix-mailcow:25` and `redis-mailcow` must not resolve or
connect, the internet must be unreachable, and `https://nginx-mailcow/api/v1`
must answer. mailcow's own published ports must still work from outside.
