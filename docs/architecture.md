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
│                                   ▲                                      │
│  /opt/MailServer/infrastructure/registration (separate compose project)  │
│   └ registration (FastAPI + SQLite volume), fixed IP 172.22.1.240        │
│       on mailcow's network; calls https://nginx-mailcow/api/v1           │
└──────────────────────────────────────────────────────────────────────────┘
        backups ──rclone (encrypted)──► off-site storage
```

## Components

| Piece | Source | Where |
|---|---|---|
| Mail server, webmail, admin UI, TLS | mailcow-dockerized (official) | `/opt/mailcow-dockerized` |
| Invite website + invite admin | this repo, `registration/` | `infrastructure/registration/docker-compose.yml` |
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
3. The app validates the input, **claims** the invitation (atomic DB update),
   checks the address isn't a mailbox or alias, then calls
   `POST /api/v1/add/mailbox`.
4. Success → invitation marked redeemed. Failure → claim released, failure
   logged for the admin, invitation still usable.
5. The user is sent to SOGo at `https://mail.<domain>`.

The mailbox password goes straight to mailcow and is never stored by the app.

## Why no Caddy

mailcow's nginx already terminates TLS with Let's Encrypt for
`mail.<domain>`. The invite site is a path on the same host, so a second
proxy would only add another certificate manager and port conflicts.
