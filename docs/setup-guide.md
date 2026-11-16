# Setup guide: Avedeus Mail on Oracle Cloud

Step-by-step, from an empty Oracle VM to people signing up with invitations.
Written for **avedeus.ovh** (DNS on Cloudflare) and the Oracle Cloud ARM VM
(Ubuntu 24.04, London). Each part ends with a check. Don't skip the checks:
a later part assumes the earlier one works.

| Part | What | Roughly |
|---|---|---|
| [A](#part-a--decisions-and-requests-before-the-server) | Decisions and Oracle requests | 30 min + Oracle's wait |
| [B](#part-b--install-mailcow) | Install mailcow | 45 min |
| [C](#part-c--domain-dns-and-real-email) | Domain, DNS, real email | 1 h + DNS wait |
| [D](#part-d--invite-website) | Invite website and branding | 30 min |
| [E](#part-e--backups-and-a-restore-test) | Backups and a restore test | 1 h |
| [F](#part-f--go-live-checklist) | Go-live checklist | 15 min |

Commands run on the server as the `ubuntu` user with `sudo`. Replace
`<IP>` with the server's reserved public IP.

## Server size: test vs production

The first deployment is a **test with one user**. Any host works if it allows
outbound port 25, lets you set reverse DNS (PTR), and runs Ubuntu 24.04
(x86 or ARM). The Oracle-specific steps in Part A have equivalents on every
host: a fixed IP, a firewall, and a PTR setting.

| Profile | RAM | Disk | mailcow settings | Keycloak validation? |
|---|---|---|---|---|
| **Test, mailcow only** | 4 GB + 2 GB swap | 40 GB | `SKIP_CLAMD=y`, `SKIP_FTS=y`, `SOGO` defaults | No |
| **Test incl. Keycloak gate** | 8 GB | 40 GB | `SKIP_CLAMD=y` (optional), `SKIP_FTS=y` | Yes |
| Production (small group) | 8–12 GB | 100 GB | ClamAV and full-text search on | If it passed |

For the 4 GB profile, after `generate_config.sh` in B3 also run:

```sh
sudo sed -i 's/^SKIP_CLAMD=.*/SKIP_CLAMD=y/; s/^SKIP_FTS=.*/SKIP_FTS=y/' mailcow.conf
```

Measure memory with `free -m` and `docker stats --no-stream` after Part D and
record it; that number decides the production size.

---

## Part A — Decisions and requests (before the server)

### A1. Which addresses: `@avedeus.ovh` or a subdomain?

Today the domain's mail goes to **OVH** (MX `mx1/mx2/mx3.mail.ovh.net`, SPF
`include:mx.ovh.com`). Moving MX to this server sends **all** mail for
`@avedeus.ovh` here instead.

1. In the OVH control panel open *Web Cloud → Emails* (or *Email redirections*)
   for avedeus.ovh. Note any mailboxes, redirections or aliases.
2. Decide:
   - **Nothing in use** → use `@avedeus.ovh` (recommended). Continue with this guide.
   - **Something in use, can move** → note each address; in Part C you create
     the same address in mailcow (and can copy old mail with mailcow's *Sync jobs*)
     before switching MX.
   - **Must keep OVH mail** → use a subdomain such as `@m.avedeus.ovh`: replace
     `avedeus.ovh` with `m.avedeus.ovh` in every *mail domain* below (MX, SPF,
     DKIM, DMARC, mailcow domain, `MAIL_DOMAIN`). The host stays `mail.avedeus.ovh`.

Also in **Cloudflare → avedeus.ovh → Email → Email Routing**: make sure it is
**disabled**; it rewrites MX records.

### A2. Oracle: reserved IP

*Networking → IP management → Reserved public IPs → Reserve*, then on the
instance: *Attached VNICs → VNIC → IPv4 addresses → Edit → Reserved public IP*.
An ephemeral IP changes if the instance is stopped, which breaks DNS and PTR.

### A3. Oracle: firewall (security list)

*Networking → Virtual cloud networks → (your VCN) → Security lists → Default
security list → Add ingress rules*, source `0.0.0.0/0`, protocol TCP:

| Port | Why |
|---|---|
| 22 | SSH. Better: source = your home IP/32 instead of 0.0.0.0/0 |
| 25 | Incoming mail from other servers |
| 80 | Let's Encrypt and redirect to https |
| 443 | Webmail, admin, invite site |
| 465, 587 | Mail apps sending |
| 993 | Mail apps reading (IMAPS) |
| 4190 | Optional: mail filters from desktop apps |

Do **not** open 110, 143 or 995.

### A4. Oracle: outbound port 25

New Oracle tenancies can't send on port 25, so the server could receive but
not send to Gmail and Outlook. Open a **service limits request** (*Help → Create
support request*, or *Governance → Limits, quotas and usage → Request a service
limit update*) asking for the **outbound SMTP port 25 exemption**. Say it is a
small self-hosted mail server with SPF, DKIM and DMARC, and give the reserved IP.
Oracle may require a Pay As You Go account; Always Free resources stay free after
upgrading. If refused, see [Sending through a relay](#if-port-25-is-refused-relay).

### A5. Cloudflare: the mail host

*Cloudflare → avedeus.ovh → DNS → Records → Add record*:

| Type | Name | Content | Proxy |
|---|---|---|---|
| A | `mail` | `<IP>` | **DNS only** (grey cloud) |

Don't change MX yet.

### A6. Oracle: reverse DNS (PTR)

Once `mail.avedeus.ovh` resolves to `<IP>` (`dig +short mail.avedeus.ovh`),
open a service request asking for a **PTR record** `<IP> → mail.avedeus.ovh`.

**Check A:** `dig +short mail.avedeus.ovh` shows `<IP>`; both Oracle requests are filed.

---

## Part B — Install mailcow

### B1. Inspect (read-only)

```sh
ssh ubuntu@<IP>
sudo apt-get update && sudo apt-get -y install git
sudo git clone https://github.com/GeneralKnowledge/MailServer /opt/MailServer
cd /opt/MailServer && sudo git checkout <branch-or-main>
sudo bash scripts/m0-inspect.sh avedeus.ovh mail.avedeus.ovh
```

Look for: `aarch64`, ~12 GB RAM, ~100 GB disk, Ubuntu 24.04, nothing listening
on 25/80/443, and the **port 25 banner** (no banner = still blocked; fine for now).
Send the `m0-report-*.txt` file to the project so it can be recorded.

### B2. Host setup

```sh
sudo /opt/MailServer/scripts/setup.sh mail.avedeus.ovh <mailcow-tag>
```

`<mailcow-tag>` is the newest release at
https://github.com/mailcow/mailcow-dockerized/releases. The script updates
Ubuntu, sets the hostname and time zone, adds swap, hardens SSH (keys only;
skipped if you have no key), turns on automatic security updates, installs
Docker, and checks out mailcow. **Before closing your SSH session**, open a
second one to prove you can still log in.

### B3. Configure mailcow

```sh
cd /opt/mailcow-dockerized
sudo MAILCOW_HOSTNAME=mail.avedeus.ovh MAILCOW_TZ=Europe/London ./generate_config.sh
sudo /opt/MailServer/scripts/harden-mailcow-conf.sh
```

Then set the API key that the provisioning broker will use (mailcow reads it at start).
Only the broker container, at `172.30.240.11`, may use it:

```sh
KEY=$(openssl rand -hex 32); echo "Save this API key: $KEY"
sudo sed -i "s|^#\?API_KEY=.*|API_KEY=$KEY|; s|^#\?API_ALLOW_FROM=.*|API_ALLOW_FROM=172.30.240.11|" mailcow.conf
grep -E '^(MAILCOW_HOSTNAME|API_ALLOW_FROM|IMAP_PORT|POP_PORT|POPS_PORT|ENABLE_IPV6|SKIP_CLAMD)=' mailcow.conf
```

If B1 showed no IPv6 address, also set `ENABLE_IPV6=false`.

### B4. Start

```sh
sudo docker compose pull
sudo docker compose up -d
sudo docker compose logs -f acme-mailcow     # Ctrl-C once you see the certificate issued
```

### B5. Lock down the admin

At `https://mail.avedeus.ovh/admin` (user `admin`, password `moohoo`):

1. **Change the password** (top right → Edit) to a long unique one.
2. **Two-factor:** same page → *Two-factor authentication → TOTP*, scan with your
   authenticator app.
3. *System → Configuration → Options → Password policy*: length 12, require
   lowercase, uppercase and numbers.
4. *System → Configuration → Access → API*: confirm the read-write key shows
   `172.30.240.11` only (the broker), and the read-only key is off (it is
   turned on later only for the message trace; see administration.md).

**Check B:** `sudo /opt/MailServer/scripts/health-check.sh` ends with `HEALTHY`
(the registration line fails until Part D and the backup lines until Part E;
ignore those). The padlock on
`https://mail.avedeus.ovh` shows a Let's Encrypt certificate.

---

## Part C — Domain, DNS and real email

### C1. Add the domain in mailcow

*E-Mail → Configuration → Domains → Add domain*: `avedeus.ovh`, max mailboxes
e.g. 50, total quota e.g. 80 GB, default mailbox quota 2048 MB. Then
*Configuration → ARC/DKIM keys*: domain `avedeus.ovh`, selector `dkim`,
2048 bits → *Add*, and copy the long `v=DKIM1;...` value.

### C2. Mailboxes moved from OVH (only if A1 found any)

Create each address as a mailbox or alias now, before switching MX.

### C3. Cloudflare records

Lower the TTL of the existing MX records to 5 minutes and wait an hour first.
Then in Cloudflare (all **DNS only**):

| Type | Name | Content | Notes |
|---|---|---|---|
| MX | `@` | `mail.avedeus.ovh`, priority 10 | **Delete** the three `mx*.mail.ovh.net` records |
| TXT | `@` | `v=spf1 mx -all` | **Replace** the `include:mx.ovh.com` record, don't add a second SPF |
| TXT | `dkim._domainkey` | the value from C1 | |
| TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:postmaster@avedeus.ovh` | tighten later |
| CNAME | `autodiscover` | `mail.avedeus.ovh` | |
| CNAME | `autoconfig` | `mail.avedeus.ovh` | |
| SRV | `_autodiscover._tcp` | priority 0, weight 1, port 443, target `mail.avedeus.ovh` | |

Leave `www`, the apex and the OVH `1|www.avedeus.ovh` TXT alone. Create an
alias `postmaster@avedeus.ovh` (and `abuse@`) to your own mailbox so DMARC
reports and complaints arrive somewhere.

mailcow's own check: *Domains → avedeus.ovh → DNS* should be all green.

### C4. Test for real

Create two test mailboxes in mailcow, then work through
[email-testing.md](email-testing.md): internal mail, receiving from Gmail and
Outlook, sending to them (inbox, not spam; SPF/DKIM/DMARC pass), mail-tester.com
score, IMAP and SMTP from a phone, SOGo in a phone browser.

#### If port 25 is refused: relay

Sign up for a relay (e.g. Brevo, Mailgun, SMTP2GO or OCI Email Delivery), verify
avedeus.ovh with them, then in mailcow *Configuration → Routing → Sender-dependent
transports* add `[smtp.relay.example]:587` with the relay's username/password,
and set it on the avedeus.ovh domain. Add the relay's `include:` to SPF.

**Check C:** every line of email-testing.md filled in with a result.

---

## Part D — Invite website

```sh
cd /opt/MailServer/infrastructure/registration
sudo cp .env.example .env && sudo cp broker.env.example broker.env && sudo chmod 600 .env broker.env
openssl rand -hex 32                                                        # SIGNUP_TOKEN in broker.env = BROKER_TOKEN in .env
openssl rand -hex 32                                                        # MONITORING_TOKEN in broker.env (a different value)
sudo docker compose build
sudo docker compose run --rm registration python -m app.cli hash-password   # paste result as ADMIN_PASSWORD_HASH='…'
sudo docker compose run --rm registration python -m app.cli new-totp       # ADMIN_TOTP_SECRET; scan the otpauth URI
openssl rand -base64 48                                                     # SESSION_SECRET
sudo nano broker.env .env
```

In `broker.env`: `MAILCOW_API_KEY` = the key from B3, `SIGNUP_TOKEN` and
`MONITORING_TOKEN` (the health check uses it).
This is the only place the key goes; the registration app refuses to start
if it finds `MAILCOW_API_KEY` in its own `.env`.

In `.env`: `BROKER_TOKEN` = the same value as `SIGNUP_TOKEN`; `ADMIN_USERNAME` = a name of
your choice; wrap the password hash in single quotes;
`ADMIN_ALLOWED_IPS=<your home IP or VPN range>` so the admin page is invisible
to everyone else (it returns 404). The app refuses to start without it unless
you set `ADMIN_ALLOW_ANY=1`; if your home IP changes, update it here and run
`docker compose up -d`, and use `python -m app.cli` over SSH meanwhile.

```sh
sudo /opt/MailServer/scripts/create-edge-network.sh   # avedeus-edge, must exist before mailcow restarts
sudo /opt/MailServer/scripts/apply-branding.sh
sudo cp /opt/MailServer/infrastructure/mailcow/configuration/nginx/site.registration.custom \
        /opt/mailcow-dockerized/data/conf/nginx/
sudo cp /opt/MailServer/infrastructure/mailcow/configuration/docker-compose.override.yml \
        /opt/mailcow-dockerized/
cd /opt/mailcow-dockerized && sudo docker compose up -d
cd /opt/MailServer/infrastructure/registration && sudo docker compose up -d
```

Then:

1. `https://mail.avedeus.ovh/join/admin` → sign in → create an invitation.
2. Open the link in a private window → choose `testinvite` → create.
3. Sign in to webmail as `testinvite@avedeus.ovh`; send yourself a message.
4. Open the same link again: it must say it's already been used.
5. Revoke an unused invitation and try it: must say withdrawn.
6. The real-API checks in [registration/README.md](../registration/README.md#real-api-check-m3-on-the-server).

**Check D:** health check `HEALTHY` apart from the backup lines; the
Avedeus logo and plum colours on the login page and in webmail, light and dark.

---

## Part E — Backups and a restore test

1. Choose off-site storage (any rclone backend: OCI Object Storage in another
   region, Backblaze B2, a Hetzner Storage Box). Create the remote, then a
   **crypt** remote on top of it: `sudo rclone config`.
2. **Save `/root/.config/rclone/rclone.conf` in your password manager.**
   Without it the backups can't be decrypted.
3. Configure and schedule:
   ```sh
   sudo mkdir -p /etc/mailserver
   printf 'RCLONE_REMOTE=offsite-crypt:mailserver\nLOCAL_KEEP_DAYS=3\nREMOTE_KEEP_DAYS=30\n' | sudo tee /etc/mailserver/backup.env
   echo '15 3 * * * root /opt/MailServer/scripts/backup.sh >>/var/log/mail-backup.log 2>&1' | sudo tee /etc/cron.d/mail-backup
   echo '*/15 * * * * root /opt/MailServer/scripts/health-check.sh >/var/log/mail-health.log 2>&1' | sudo tee /etc/cron.d/mail-health
   echo '10 4 * * * root /opt/MailServer/scripts/reconcile.sh >>/var/log/mail-reconcile.log 2>&1' | sudo tee /etc/cron.d/mail-reconcile
   sudo /opt/MailServer/scripts/backup.sh
   ```
4. **Alerts.** Install the [ntfy](https://ntfy.sh) app on your phone and
   subscribe to a long random topic name. At [healthchecks.io](https://healthchecks.io)
   (free) create two checks: *backup* (period 1 day, grace 2 h) and *health*
   (period 15 min, grace 30 min), each alerting to your personal email. Then:
   ```sh
   sudo tee /etc/mailserver/alerts.env >/dev/null <<'CONF'
   NTFY_URL=https://ntfy.sh/<your-long-random-topic>
   ALERT_EMAIL=<your personal address, not @avedeus.ovh>
   HC_BACKUP_URL=https://hc-ping.com/<backup check uuid>
   HC_HEALTH_URL=https://hc-ping.com/<health check uuid>
   CONF
   sudo chmod 600 /etc/mailserver/alerts.env
   sudo /opt/MailServer/scripts/health-check.sh   # sends one alert now if anything is wrong
   ```
   The health check alerts once when a problem appears and once when it
   clears. healthchecks.io emails you if a backup or health run fails or
   simply doesn't happen (server down, cron broken). None of the alerts go
   through this mail server.
5. **Restore test** (required before calling it done): follow
   [backup-and-recovery.md](backup-and-recovery.md#restore-same-or-new-server),
   ideally onto a second temporary VM, and record the result in its table.

**Check E:** a dated folder appears on the remote; both healthchecks.io
checks are green; `docker stop sogo-mailcow` → one push alert within 15
minutes, `docker start` → one "all clear"; the restore test row is filled in.

---

## Part F — Go-live checklist

- [ ] mailcow admin: password changed, TOTP on
- [ ] Invite admin: TOTP set, `ADMIN_ALLOWED_IPS` set (or `ADMIN_ALLOW_ANY=1` as a conscious choice)
- [ ] `setup.sh` printed "sshd hardened and verified"
- [ ] SSH: key-only, port 22 limited to your IP in the security list
- [ ] Only the ports in A3 are open (check from outside: `nc -zv mail.avedeus.ovh 143` must fail)
- [ ] PTR shows `mail.avedeus.ovh` (`dig -x <IP> +short`)
- [ ] email-testing.md complete; Gmail and Outlook land in the inbox
- [ ] Backups running nightly, restore test passed
- [ ] After two clean weeks of DMARC reports: change `_dmarc` to `p=quarantine`
- [ ] Optional: MTA-STS (TXT `_mta-sts` + CNAME `mta-sts` → `mail.avedeus.ovh`; mailcow serves the policy)
- [ ] Test mailboxes deleted; first real invitations sent

## After go-live

- Monthly: `cd /opt/mailcow-dockerized && sudo ./update.sh` after a backup,
  then `sudo /opt/MailServer/scripts/apply-branding.sh` and the health check.
- Invite site updates: `cd /opt/MailServer && sudo git pull && cd infrastructure/registration && sudo docker compose up -d --build`.
- Day-to-day tasks: [administration.md](administration.md).
