# Deployment

For a step-by-step walkthrough with avedeus.ovh, use [setup-guide.md](setup-guide.md); this page is the reference.

Order: M0 inspection → M1 (this page, sections 1–4) → M2 → M3 (section 5).
Replace `example.com` throughout with the real domain.

## 1. Prerequisites

See [m0-readiness-report.md](m0-readiness-report.md): reserved public IP,
OCI ingress rules, port 25 exemption request, `A mail.example.com` record.

## 2. Host and Docker

```sh
sudo git clone https://github.com/GeneralKnowledge/MailServer /opt/MailServer
sudo /opt/MailServer/scripts/setup.sh mail.example.com <mailcow-release-tag>
```

Pick the latest tag from https://github.com/mailcow/mailcow-dockerized/releases.

## 3. mailcow

```sh
cd /opt/mailcow-dockerized
sudo MAILCOW_HOSTNAME=mail.example.com MAILCOW_TZ=Europe/London ./generate_config.sh
sudo nano mailcow.conf
```

Settings to check in `mailcow.conf`:

| Setting | Value | Why |
|---|---|---|
| `MAILCOW_HOSTNAME` | `mail.example.com` | |
| `SKIP_LETS_ENCRYPT` | `n` | built-in ACME |
| `SKIP_CLAMD`, `SKIP_FTS` | `n` | 12 GB RAM is enough; set `y` if memory gets tight |
| `ENABLE_IPV6` | `false` if the VM has no IPv6 | avoids broken v6 delivery |
| `HTTP_REDIRECT` | `y` | send http to https |

Keep a redacted copy of what you changed in
`infrastructure/mailcow/configuration/mailcow.conf.notes` (no passwords).

```sh
sudo docker compose pull     # confirms arm64 images exist
sudo docker compose up -d
sudo docker compose logs -f acme-mailcow   # wait for the certificate
```

Then at `https://mail.example.com/admin`: change the `admin` / `moohoo`
password **immediately** and enable two-factor authentication
(Access → Edit admin → TOTP).

## 4. Check

```sh
sudo /opt/MailServer/scripts/health-check.sh mail.example.com
```

## 5. Registration app (M3)

1. **API key.** mailcow UI → System → Configuration → Access → API:
   enable the **read-write** key, set *Allow API access from these IPs* to
   `172.30.240.11` (the provisioning broker), copy the key. mailcow keys
   aren't scoped to endpoints, so only the broker holds it; the public app
   can only ask the broker to check an address or create a mailbox.
2. **Config.**
   ```sh
   cd /opt/MailServer/infrastructure/registration
   sudo cp .env.example .env && sudo cp broker.env.example broker.env && sudo chmod 600 .env broker.env
   openssl rand -hex 32                                                        # -> SIGNUP_TOKEN (broker.env) = BROKER_TOKEN (.env)
   sudo docker compose build
   openssl rand -base64 48                                                     # -> SESSION_SECRET
   sudo nano broker.env   # MAILCOW_API_KEY, SIGNUP_TOKEN, MONITORING_TOKEN (its own value)
   sudo nano .env         # BROKER_TOKEN, domain, secrets
   ```
3. **Start.** `sudo docker compose up -d`
4. **Proxy.**
   ```sh
   sudo cp /opt/MailServer/infrastructure/mailcow/configuration/nginx/site.registration.custom \
           /opt/mailcow-dockerized/data/conf/nginx/
   cd /opt/mailcow-dockerized && sudo docker compose restart nginx-mailcow
   ```
5. **Branding.** Edit `/opt/MailServer/branding/` if needed, then
   ```sh
   sudo /opt/MailServer/scripts/apply-branding.sh       # writes the files the override below mounts
   sudo cp /opt/MailServer/infrastructure/mailcow/configuration/docker-compose.override.yml \
           /opt/mailcow-dockerized/                     # merge by hand if one exists
   cd /opt/mailcow-dockerized && sudo docker compose up -d
   cd /opt/MailServer/infrastructure/registration && sudo docker compose up -d --build
   ```
   Run `apply-branding.sh` before the first `up -d` with the override, or
   Docker creates a directory where the empty CSS file should be. The script
   brands the invite site, SOGo, and mailcow's own login page (logo, names,
   plum buttons), which is where `/SOGo` sends people to sign in.
6. Create a test invitation (`sudo docker compose exec registration python -m app.cli create-invite`),
   register with it, then log in to SOGo with the new address. The web admin
   console (`/console/`) comes with Keycloak ([admin-console.md](admin-console.md)).

## Updates

Every update starts from a fresh, verified backup:
`sudo /opt/MailServer/scripts/backup.sh` (and, for a major mailcow release,
`sudo /opt/MailServer/scripts/restore-drill.sh` on it).

**mailcow** (monthly, or on a security release):

1. `cd /opt/mailcow-dockerized && sudo ./update.sh --check`; read the release
   notes for anything about the options, files or API we use
   (`docker-compose.override.yml`, `data/conf/dovecot/extra.conf`, the nginx
   includes, Keycloak identity provider, quota notifications, `force_sso`).
2. `sudo ./update.sh`. It commits local changes as "Before update on …" first.
3. `sudo /opt/MailServer/scripts/apply-branding.sh`: mailcow tracks the SOGo
   theme and logo files in git, and `update.sh` merges with "theirs", so an
   upstream change to them replaces ours. Then `sudo docker compose up -d`, so
   the override is applied again.
4. Check: `sudo /opt/MailServer/scripts/health-check.sh`, sign in to webmail
   (through Keycloak once it is deployed), send and receive one message, and
   for a broker change `docker compose exec broker python -m broker.probe`.
5. Roll back only together with the database backup: an update can change
   mailcow's database. `docker compose down`, `git checkout <the "Before
   update" commit>` (`git log --oneline | grep -i "before update"`),
   `docker compose pull`, restore the backup (`scripts/restore.sh mailcow`),
   `docker compose up -d`.

**Keycloak** (after the ADR 0006 gate): the version is pinned in
`infrastructure/identity/docker-compose.yml`. Change it in a pull request, so
CI's identity stack job starts the new version, imports the realm and runs the
live tests against it. On the server: backup, `git pull`, then
`docker compose -f infrastructure/identity/docker-compose.yml --env-file infrastructure/identity/identity.env up -d`,
and sign in to the portal and the admin console. Keycloak migrates its
database on start; to go back, restore its dump
(`scripts/restore.sh identity <backup>`) with the old image.

**Our apps** (registration, broker, portal, admin console):
`cd /opt/MailServer && sudo git pull && cd infrastructure/registration && sudo docker compose up -d --build`.
The control plane's database needs no migration step: tables are created if
missing, and the restore drill opens old backups with the current code.

**Ubuntu:** `unattended-upgrades` installs security updates; reboot when
`/var/run/reboot-required` exists.

## Logs

```sh
cd /opt/mailcow-dockerized && sudo docker compose logs --tail=200 postfix-mailcow
cd /opt/MailServer/infrastructure/registration && sudo docker compose logs --tail=200
```
The mailcow UI also has per-service logs under System → Logs.
