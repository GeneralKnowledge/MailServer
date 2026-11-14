# Backup and recovery

## What is backed up

`scripts/backup.sh` runs daily and produces `/var/backups/mailcow/mailcow-<date>/`:

| Content | File |
|---|---|
| Mailboxes | `backup_vmail.tar.zst` |
| Mail encryption keys (required to read vmail) | `backup_crypt.tar.zst` |
| Redis (includes **DKIM keys**) | `backup_redis.tar.zst` |
| MariaDB (domains, mailboxes, aliases, settings) | `backup_mariadb.tar.zst` |
| Rspamd, Postfix data | `backup_rspamd.tar.zst`, `backup_postfix.tar.zst` |
| mailcow.conf | `mailcow.conf` |
| Invitation database | `extras/registration.sqlite3` |
| Registration `.env` (secrets) | `extras/registration.env` |
| nginx custom site, SOGo branding, TLS certs | `extras/mailcow-custom.tgz` |
| Checksums | `SHA256SUMS` |

The mailcow parts come from mailcow's official
`helper-scripts/backup_and_restore.sh`.

## Schedule and retention

- Daily at 03:15 (cron line in `scripts/backup.sh`).
- 3 days kept on the server, 30 days off-site (`/etc/mailserver/backup.env`).
- Verification each run: every archive is tested with `zstd -t`, required
  files must exist, the SQLite copy must pass `integrity_check`, and the
  upload is checked with `rclone check`. Failure → non-zero exit, nothing
  uploaded, logged in `/var/log/mail-backup.log`.

## Off-site storage

Any rclone backend; use an rclone **crypt** remote on top so the provider
can't read mail. Candidates: OCI Object Storage in another region (S3 API),
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
   without `docker-compose.override.yml`.
3. Unpack `extras/mailcow-custom.tgz` over `/opt/mailcow-dockerized` if the
   nginx/SOGo customisations, the login-page stylesheet or
   `docker-compose.override.yml` are missing; then `docker compose up -d`.
   (The login-page logos and names are in Redis, which the mailcow restore
   brings back.)
4. Registration: `sudo /opt/MailServer/scripts/restore.sh registration mailcow-YYYY-…`
   (works on an empty volume too; it creates the container if needed).
5. Run `scripts/health-check.sh` and the checks in
   [email-testing.md](email-testing.md).

## Restore test log

The project isn't complete until at least one restore has succeeded.

| Date | Backup used | Target | Result |
|---|---|---|---|
| 2026-11-14 | Rehearsal backup, fetched from an rclone crypt remote | Local rehearsal mailcow (x86, not the real server), all volumes deleted first | Mailboxes, alias, messages and invitations restored; IMAP/SMTP login and a new sign-up worked afterwards. See [rehearsal-2026-11-14.md](rehearsal-2026-11-14.md). Still to do on the real server. |
