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
   sudo docker compose run --rm registration python -m app.cli hash-password   # -> ADMIN_PASSWORD_HASH
   sudo docker compose run --rm registration python -m app.cli new-totp       # -> ADMIN_TOTP_SECRET, scan the URI
   openssl rand -base64 48                                                     # -> SESSION_SECRET
   sudo nano broker.env   # MAILCOW_API_KEY, SIGNUP_TOKEN, MONITORING_TOKEN (its own value)
   sudo nano .env         # BROKER_TOKEN, domain, secrets
   ```
   In `.env`, the hash contains `$` characters; wrap it in single quotes.
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
6. Open `https://mail.example.com/join/admin`, sign in, create a test
   invitation, register with it, then log in to SOGo with the new address.

## Updates

- **mailcow:** `cd /opt/mailcow-dockerized && sudo ./update.sh --check`, then
  `sudo ./update.sh` after a backup. Then re-run
  `sudo /opt/MailServer/scripts/apply-branding.sh`: mailcow tracks the SOGo
  theme and logo files in git, and `update.sh` merges with "theirs", so an
  upstream change to them replaces ours. Re-run the health check.
- **Registration app:** `cd /opt/MailServer && sudo git pull &&
  cd infrastructure/registration && sudo docker compose up -d --build`.
- **Ubuntu:** `unattended-upgrades` installs security updates; reboot when
  `/var/run/reboot-required` exists.

## Logs

```sh
cd /opt/mailcow-dockerized && sudo docker compose logs --tail=200 postfix-mailcow
cd /opt/MailServer/infrastructure/registration && sudo docker compose logs --tail=200
```
The mailcow UI also has per-service logs under System → Logs.
