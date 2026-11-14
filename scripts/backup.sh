#!/usr/bin/env bash
# Daily backup: mailcow (official helper), registration DB and our config,
# then verify archives and copy everything off-server with rclone.
#
# Config: /etc/mailserver/backup.env, e.g.
#   BACKUP_DIR=/var/backups/mailcow
#   RCLONE_REMOTE=offsite-crypt:mailserver     # an rclone *crypt* remote (encrypted)
#   LOCAL_KEEP_DAYS=3
#   REMOTE_KEEP_DAYS=30
# Cron (03:15 daily):
#   15 3 * * * root /opt/MailServer/scripts/backup.sh >>/var/log/mail-backup.log 2>&1
set -euo pipefail
[ -f /etc/mailserver/backup.env ] && . /etc/mailserver/backup.env
MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/mailcow}"
LOCAL_KEEP_DAYS="${LOCAL_KEEP_DAYS:-3}"
REMOTE_KEEP_DAYS="${REMOTE_KEEP_DAYS:-30}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
log() { echo "$(date -u +%FT%TZ) $*"; }

# mailcow's generate_config.sh leaves DOCKER_COMPOSE_VERSION empty until the
# first update.sh, and backup_and_restore.sh's restore refuses to run then.
fix_compose_version() {
  local conf="$MAILCOW_DIR/mailcow.conf"
  if grep -q '^DOCKER_COMPOSE_VERSION=$' "$conf" 2>/dev/null && docker compose version >/dev/null 2>&1; then
    sed -i 's/^DOCKER_COMPOSE_VERSION=$/DOCKER_COMPOSE_VERSION=native/' "$conf"
    echo "Set DOCKER_COMPOSE_VERSION=native in $conf (was empty)"
  fi
}

fix_compose_version
mkdir -p "$BACKUP_DIR"
chmod 1777 "$BACKUP_DIR"   # mailcow's helper requires it to be writable by its backup container

log "mailcow backup (all components) -> $BACKUP_DIR"
before=$(ls -1d "$BACKUP_DIR"/mailcow-* 2>/dev/null | sort | tail -1 || true)
MAILCOW_BACKUP_LOCATION="$BACKUP_DIR" THREADS=2 \
  "$MAILCOW_DIR/helper-scripts/backup_and_restore.sh" backup all --delete-days "$LOCAL_KEEP_DAYS"
latest=$(ls -1d "$BACKUP_DIR"/mailcow-* | sort | tail -1)
[ -n "$latest" ] && [ "$latest" != "$before" ] || { log "ERROR: no new mailcow backup folder"; exit 1; }

log "extras -> $latest/extras"
mkdir -p "$latest/extras"
# Registration database (consistent online copy) + its .env
if docker ps --format '{{.Names}}' | grep -q '^registration-registration'; then
  docker compose -f "$REPO_DIR/infrastructure/registration/docker-compose.yml" exec -T registration \
    python -m app.cli backup /data/backup.sqlite3
  docker compose -f "$REPO_DIR/infrastructure/registration/docker-compose.yml" cp \
    registration:/data/backup.sqlite3 "$latest/extras/registration.sqlite3"
fi
cp -a "$REPO_DIR/infrastructure/registration/.env" "$latest/extras/registration.env" 2>/dev/null || true
# Our customisations inside mailcow (nginx site, SOGo theme/logos, login page
# stylesheet, compose override) and mailcow.conf
tar -C "$MAILCOW_DIR" -czf "$latest/extras/mailcow-custom.tgz" --ignore-failed-read \
  mailcow.conf docker-compose.override.yml data/conf/nginx/ data/conf/sogo/ \
  data/web/css/build/0081-custom-mailcow.css data/assets/ssl/ 2>/dev/null || true
chmod -R go-rwx "$latest/extras"

log "verify"
fail=0
for f in "$latest"/*.tar.zst; do
  if zstd -tq "$f"; then echo "  ok $(basename "$f") $(du -h "$f" | cut -f1)"; else echo "  CORRUPT $f"; fail=1; fi
done
for need in backup_vmail.tar.zst backup_crypt.tar.zst backup_redis.tar.zst backup_mariadb.tar.zst mailcow.conf; do
  [ -s "$latest/$need" ] || { echo "  MISSING $need"; fail=1; }
done
if [ -f "$latest/extras/registration.sqlite3" ]; then
  python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); assert c.execute('pragma integrity_check').fetchone()[0]=='ok'; print('  ok registration.sqlite3', c.execute('select count(*) from invitations').fetchone()[0], 'invitations')" "$latest/extras/registration.sqlite3" || fail=1
fi
( cd "$latest" && find . -type f ! -name SHA256SUMS -print0 | xargs -0 sha256sum > SHA256SUMS )
[ "$fail" -eq 0 ] || { log "ERROR: verification failed, not uploading"; exit 1; }

if [ -n "$RCLONE_REMOTE" ]; then
  log "upload -> $RCLONE_REMOTE/$(basename "$latest")"
  rclone copy "$latest" "$RCLONE_REMOTE/$(basename "$latest")" --transfers 4
  # A crypt remote has no hashes to compare; cryptcheck verifies contents.
  if [ "$(rclone config dump | jq -r --arg r "${RCLONE_REMOTE%%:*}" '.[$r].type')" = crypt ]; then
    rclone cryptcheck "$latest" "$RCLONE_REMOTE/$(basename "$latest")" --one-way
  else
    rclone check "$latest" "$RCLONE_REMOTE/$(basename "$latest")" --one-way
  fi
  log "remote retention: delete folders older than $REMOTE_KEEP_DAYS days"
  rclone delete "$RCLONE_REMOTE" --min-age "${REMOTE_KEEP_DAYS}d" && rclone rmdirs "$RCLONE_REMOTE" --leave-root
else
  log "WARNING: RCLONE_REMOTE not set; backup is only on this server"
fi
log "done: $latest"
