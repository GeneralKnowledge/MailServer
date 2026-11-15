# Backup and recovery

## What is backed up

`scripts/backup.sh` runs daily and produces `/var/backups/mailserver/mailcow/mailcow-<date>/`:

| Content | File |
|---|---|
| Mailboxes | `backup_vmail.tar.zst` |
| Mail encryption keys (required to read vmail) | `backup_crypt.tar.zst` |
| Redis (includes **DKIM keys**) | `backup_redis.tar.zst` |
| MariaDB (domains, mailboxes, aliases, settings) | `backup_mariadb.tar.zst` |
| Rspamd, Postfix data | `backup_rspamd.tar.zst`, `backup_postfix.tar.zst` |
| mailcow.conf | `mailcow.conf` |
| Invitation database | `extras/registration.sqlite3` |
| Control-plane database (audit log, idempotency records; PR 9) | `extras/control-plane.sqlite3` |
| Registration `.env` and broker `broker.env` (secrets, incl. the mailcow API key) | `extras/registration.env`, `extras/broker.env` |
| Keycloak's database and `identity.env` (PR 11; only with `IDENTITY_ENABLED=1`, i.e. once Keycloak is deployed after the ADR 0006 gate) | `extras/keycloak.pgdump`, `extras/identity.env` |
| nginx custom site, SOGo branding, TLS certs | `extras/mailcow-custom.tgz` |
| Checksums | `SHA256SUMS` |

The mailcow parts come from mailcow's official
`helper-scripts/backup_and_restore.sh`.

Permissions: mailcow's helper insists that its backup folder is writable by
everyone and writes archives readable by everyone. The scripts therefore keep
everything under `/var/backups/mailserver` (mode 700, root only) and lock each
new backup folder to root after the helper finishes. Downloads for a restore go
to `/var/backups/mailserver/restore`, also root only.

## Schedule and retention

- Daily at 03:15 (cron line in `scripts/backup.sh`).
- 3 days kept on the server, 30 days off-site (`/etc/mailserver/backup.env`).
- Verification each run: every archive is tested with `zstd -t`, required
  files must exist (including the invitation database, its `.env` and
  `mailcow-custom.tgz`), the SQLite copy must pass `integrity_check`, the
  Keycloak dump (when enabled) must list its tables with `pg_restore --list`, and the
  upload is checked with `rclone cryptcheck`. Failure → non-zero exit, nothing
  uploaded, logged in `/var/log/mail-backup.log`.
- Alerts: a failed run sends a push alert (ntfy) and pings healthchecks.io's
  `/fail`; a run that never happens is caught by healthchecks.io's schedule.
  Each successful run writes `/var/backups/mailserver/last-success`, and
  `health-check.sh` reports a problem when it is older than 26 hours.
- If the registration container isn't running, the backup **fails** rather
  than quietly leaving out the invitations. On a server without the
  registration app, set `SKIP_REGISTRATION=1` in `backup.env`.

## Off-site storage

Any rclone backend, with an rclone **crypt** remote on top so the provider
can't read mail. `backup.sh` refuses to upload to a remote that isn't a crypt
remote, because a backup holds every message, the DKIM and mail-crypt keys,
the mailcow API key and the admin TOTP secret. (`ALLOW_PLAIN_REMOTE=1`
overrides this; don't.) Candidates: OCI Object Storage in another region (S3 API),
Backblaze B2, Hetzner Storage Box. To be chosen in M4.

```sh
sudo rclone config     # create e.g. "offsite" then "offsite-crypt" (type crypt)
sudo mkdir -p /etc/mailserver && sudo tee /etc/mailserver/backup.env >/dev/null <<CONF
RCLONE_REMOTE=offsite-crypt:mailserver
LOCAL_KEEP_DAYS=3
REMOTE_KEEP_DAYS=30
CONF
```

Keep a copy of `/root/.config/rclone/rclone.conf` (crypt passwords!) in
your password manager. Without it the off-site backups can't be decrypted.

## Restore (same or new server)

1. New server: run M1 steps 1–3 (setup.sh, generate_config.sh with the
   **same hostname**), but don't create anything in the UI.
2. Restore rclone config, then
   ```sh
   sudo /opt/MailServer/scripts/restore.sh list
   sudo /opt/MailServer/scripts/restore.sh fetch mailcow-YYYY-MM-DD-HH-MM-SS
   sudo /opt/MailServer/scripts/restore.sh mailcow          # choose that folder, then "all"
   ```
   The `mailcow` step fills in `DOCKER_COMPOSE_VERSION` if `generate_config.sh`
   left it empty (mailcow's restore refuses to run otherwise), and afterwards
   re-runs `docker compose up -d`, because mailcow's helper restarts mailcow
   without `docker-compose.override.yml`. `fetch` stops with an error if any
   checksum doesn't match; don't restore from that folder.
3. Unpack `extras/mailcow-custom.tgz` over `/opt/mailcow-dockerized` if the
   nginx/SOGo customisations, the login-page stylesheet or
   `docker-compose.override.yml` are missing; then `docker compose up -d`.
   (The login-page logos and names are in Redis, which the mailcow restore
   brings back.)
4. Registration: `sudo /opt/MailServer/scripts/restore.sh registration mailcow-YYYY-…`
   (works on an empty volume too; it creates the container if needed). The
   database it replaces is saved first as
   `/var/backups/mailserver/restore/registration-before-restore-<time>.sqlite3`.
   The same command restores the control plane's database (audit log) if the
   backup has one, saving the current one as `control-plane-before-restore-<time>.sqlite3`.
   Afterwards run `scripts/reconcile.sh` to see whether mailcow and the audit
   log agree.
5. Run `scripts/health-check.sh` and the checks in
   [email-testing.md](email-testing.md).

## Restore test log

The project isn't complete until at least one restore has succeeded.

| Date | Backup used | Target | Result |
|---|---|---|---|
| 2026-11-14 | Rehearsal backup, fetched from an rclone crypt remote | Local rehearsal mailcow (x86, not the real server), all volumes deleted first | Mailboxes, alias, messages and invitations restored; IMAP/SMTP login and a new sign-up worked afterwards. See [rehearsal-2026-11-14.md](rehearsal-2026-11-14.md). Still to do on the real server. |
