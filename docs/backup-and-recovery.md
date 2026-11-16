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
- A run that finishes (verified and, if set, uploaded) is recorded in the
  control plane as `backup.completed` with its start time. Account deletions
  scheduled in the admin console wait for such a backup that started after
  they were scheduled, so the last copy of a deleted mailbox is always in a
  backup (PR 16, [ADR 0021](adr/0021-account-lifecycle.md)). If recording it
  fails, the backup still counts; deletions wait for the next run.

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

## Restore drill (PR 29)

> **Implemented in code; not proven on server.** CI runs it against a
> synthetic backup with a real Keycloak dump: it must pass, and must fail once
> the dump is damaged.

`scripts/restore-drill.sh` proves a backup can be restored without touching
anything that runs. Monthly, from the off-site copy:

```sh
echo '0 6 1 * * root /opt/MailServer/scripts/restore-drill.sh --remote >>/var/log/mail-drill.log 2>&1' | sudo tee /etc/cron.d/mail-drill
sudo /opt/MailServer/scripts/restore-drill.sh            # newest local backup, now
```

It checks the checksums; reads every mailcow archive to the end; that the
mail archive holds messages and the crypt archive the mail-crypt key; that
the secrets a restore needs are there; opens the control-plane database with
the **current** broker image (in a container without network) and checks the
audit log is still append-only; checks the invitation database; and restores
Keycloak's dump into a throwaway Postgres without network and finds the
`avedeus` realm, its people and clients. Throwaway copies are deleted
afterwards. The result is in `/var/backups/mailserver/last-drill`; a failure
sends one alert.

What it does not do: restore mailcow itself (MariaDB, Redis, vmail) or start
Keycloak on the restored database. That needs a second machine, so the full
wipe-and-restore stays a manual exercise, recorded in the log below.

## One account (PR 29)

Runbooks, not tools ([ADR 0017](adr/0017-minimum-complete-platform.md)): each
uses what mailcow, Dovecot and SOGo already do. **Not proven on the server
yet**; try each once there and note the result in the log below. `A` is the
address; run from `/opt/mailcow-dockerized`.

### "I deleted messages and emptied the trash"

With lazy-expunge on (`infrastructure/mailcow/configuration/dovecot/extra.conf`,
copied to `data/conf/dovecot/extra.conf`; mailcow's own option), deleted mail
stays 7 days in a folder named `.EXPUNGED`, which appears once something is
in it. The person can move messages back themselves in webmail or their mail
app. An admin can move everything back:

```sh
docker compose exec dovecot-mailcow doveadm move -u "$A" INBOX mailbox .EXPUNGED ALL
```

Without lazy-expunge, or after 7 days: restore the account from a backup (below).

### A mailbox was deleted by mistake

mailcow keeps a deleted mailbox, still encrypted, for `MAILDIR_GC_TIME`
minutes (default 5 days) under the vmail volume's `_garbage` folder, named
`<timestamp>_<domain><user>`; SOGo keeps a daily backup of each person's
contacts, calendars and settings. Follow mailcow's
[Recover accidentally deleted data](https://docs.mailcow.email/backup_restore/b_n_r-accidental_deletion/):
re-create the mailbox (and its Keycloak user, with the same address), copy the
folder back, then

```sh
docker compose exec dovecot-mailcow doveadm force-resync -u "$A" '*'
docker compose exec dovecot-mailcow doveadm quota recalc -u "$A"
docker compose exec -u sogo sogo-mailcow sogo-tool restore -F ALL /etc/sogo "$A"   # after copying its SOGo backup file
```

An account deleted through the admin console (PR 16) was only deleted after
its grace period and a newer backup, so restore it from that backup instead.

### One account from a backup

On the same server (the mail is encrypted with this server's keys):

```sh
B=/var/backups/mailserver/mailcow/mailcow-YYYY-…      # or fetch one: scripts/restore.sh fetch
zstd -dc "$B/backup_vmail.tar.zst" | tar -t | grep "/${A#*@}/${A%@*}/" | head   # see the path inside
mkdir -p /var/backups/mailserver/restore/one && \
  zstd -dc "$B/backup_vmail.tar.zst" | tar -x -C /var/backups/mailserver/restore/one --wildcards "*/${A#*@}/${A%@*}/*"
```

Copy the extracted user folder over (or next to) the mailbox's folder in the
vmail volume, then run the `force-resync` and `quota recalc` lines above.
Messages already in the mailbox stay; restored ones are added.

### Export one account (someone leaves, or asks for their data)

First choice, by the person: their mail app over IMAP with an app password
(e.g. Thunderbird, then save folders), and in webmail the **Export** item of
each address book (`.vcf`) and calendar (`.ics`).

By an admin, when the person can't: mail as `.eml` files, decrypted by
Dovecot, contacts and calendars in SOGo's own backup format, filters as Sieve:

```sh
OUT=/var/backups/mailserver/export/$A; mkdir -p "$OUT/mail"; chmod 700 /var/backups/mailserver/export
docker compose exec -T dovecot-mailcow doveadm mailbox status -u "$A" guid '*' > "$OUT/folders.txt"
docker compose exec -T dovecot-mailcow doveadm search -u "$A" ALL |
while read -r guid uid; do
  docker compose exec -T dovecot-mailcow doveadm -f pager fetch -u "$A" text mailbox-guid "$guid" uid "$uid" </dev/null \
    | sed '1d;$d' > "$OUT/mail/$guid-$uid.eml"
done
docker compose exec -u sogo sogo-mailcow sogo-tool backup /tmp/sogo-export "$A" && \
  docker compose cp "sogo-mailcow:/tmp/sogo-export/$A" "$OUT/sogo-$A" && \
  docker compose exec sogo-mailcow rm -rf /tmp/sogo-export
docker compose exec -T dovecot-mailcow doveadm sieve list -u "$A"            # then, per script:
docker compose exec -T dovecot-mailcow doveadm sieve get -u "$A" NAME > "$OUT/NAME.sieve"
```

Check one `.eml` opens in a mail program (the `sed` strips doveadm's
`text:` header line and the record separator; adjust if the server's
doveadm prints differently). Hand the folder over encrypted, by a channel you
trust, and delete it afterwards. No export service exists; one will be built
only if this is needed often (roadmap PR 29).

## Restore test log

The project isn't complete until at least one restore has succeeded.

| Date | Backup used | Target | Result |
|---|---|---|---|
| 2026-11-14 | Rehearsal backup, fetched from an rclone crypt remote | Local rehearsal mailcow (x86, not the real server), all volumes deleted first | Mailboxes, alias, messages and invitations restored; IMAP/SMTP login and a new sign-up worked afterwards. See [rehearsal-2026-11-14.md](rehearsal-2026-11-14.md). Still to do on the real server. |
