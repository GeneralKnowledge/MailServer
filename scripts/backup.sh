#!/usr/bin/env bash
# Daily backup: mailcow (official helper), registration DB and our config,
# then verify archives and copy everything off-server with rclone.
#
# Config: /etc/mailserver/backup.env, e.g.
#   BACKUP_ROOT=/var/backups/mailserver       # kept at mode 700 (root only)
#   BACKUP_DIR=$BACKUP_ROOT/mailcow
#   RCLONE_REMOTE=offsite-crypt:mailserver     # must be an rclone *crypt* remote (encrypted)
#   ALLOW_PLAIN_REMOTE=0                       # 1 = allow a non-crypt remote (not recommended)
#   SKIP_REGISTRATION=0                        # 1 = this server doesn't run the registration app
#   LOCAL_KEEP_DAYS=3
#   REMOTE_KEEP_DAYS=30
# Alerts: /etc/mailserver/alerts.env (see scripts/lib/alerts.sh). A failed run
# sends a push alert and pings HC_BACKUP_URL/fail; a missing run is caught by
# healthchecks.io because the success ping doesn't arrive.
# Cron (03:15 daily):
#   15 3 * * * root /opt/MailServer/scripts/backup.sh >>/var/log/mail-backup.log 2>&1
set -euo pipefail
umask 077  # everything this script writes holds mail or secrets
[ -f /etc/mailserver/backup.env ] && . /etc/mailserver/backup.env
MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/mailserver}"
BACKUP_DIR="${BACKUP_DIR:-$BACKUP_ROOT/mailcow}"
LOCAL_KEEP_DAYS="${LOCAL_KEEP_DAYS:-3}"
REMOTE_KEEP_DAYS="${REMOTE_KEEP_DAYS:-30}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"
ALLOW_PLAIN_REMOTE="${ALLOW_PLAIN_REMOTE:-0}"
SKIP_REGISTRATION="${SKIP_REGISTRATION:-0}"
REG_COMPOSE="$REPO_DIR/infrastructure/registration/docker-compose.yml"
log() { echo "$(date -u +%FT%TZ) $*"; }
# shellcheck source=lib/alerts.sh
. "$REPO_DIR/scripts/lib/alerts.sh"
HC_BACKUP_URL="${HC_BACKUP_URL:-}"

# Keep a copy of this run's output for the failure alert.
RUN_LOG="$(mktemp)"
exec > >(tee -a "$RUN_LOG") 2>&1
on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    sleep 1  # let tee catch up
    hc_ping "$HC_BACKUP_URL" fail "$(tail -n 60 "$RUN_LOG")"
    alert_send high "backup FAILED" "$(tail -n 15 "$RUN_LOG")"
  fi
  rm -f "$RUN_LOG"
}
trap on_exit EXIT
hc_ping "$HC_BACKUP_URL" start

# mailcow's generate_config.sh leaves DOCKER_COMPOSE_VERSION empty until the
# first update.sh, and backup_and_restore.sh's restore refuses to run then.
fix_compose_version() {
  local conf="$MAILCOW_DIR/mailcow.conf"
  if grep -q '^DOCKER_COMPOSE_VERSION=$' "$conf" 2>/dev/null && docker compose version >/dev/null 2>&1; then
    sed -i 's/^DOCKER_COMPOSE_VERSION=$/DOCKER_COMPOSE_VERSION=native/' "$conf"
    echo "Set DOCKER_COMPOSE_VERSION=native in $conf (was empty)"
  fi
}

# Refuse to send backups (all mail, DKIM and mail-crypt keys, API key, TOTP
# secret) to a remote that doesn't encrypt them, unless explicitly allowed.
remote_type=""
if [ -n "$RCLONE_REMOTE" ]; then
  remote_type="$(rclone config dump | jq -r --arg r "${RCLONE_REMOTE%%:*}" '.[$r].type // empty')"
  [ -n "$remote_type" ] || { log "ERROR: rclone remote '${RCLONE_REMOTE%%:*}' is not configured"; exit 1; }
  if [ "$remote_type" != crypt ] && [ "$ALLOW_PLAIN_REMOTE" != 1 ]; then
    log "ERROR: $RCLONE_REMOTE is a '$remote_type' remote, not crypt; backups would be stored unencrypted."
    log "       Use an rclone crypt remote, or set ALLOW_PLAIN_REMOTE=1 to accept that."
    exit 1
  fi
fi

fix_compose_version
# mailcow's helper insists that BACKUP_DIR is writable by others, so the
# privacy comes from its parent: BACKUP_ROOT is root-only (700).
mkdir -p "$BACKUP_DIR"
case "$BACKUP_DIR" in
  "$BACKUP_ROOT"/*) chmod 700 "$BACKUP_ROOT" ;;
  *) log "WARNING: BACKUP_DIR is not under BACKUP_ROOT ($BACKUP_ROOT); make sure its parent is not readable by others" ;;
esac
chmod 1777 "$BACKUP_DIR"

log "mailcow backup (all components) -> $BACKUP_DIR"
before=$(ls -1d "$BACKUP_DIR"/mailcow-* 2>/dev/null | sort | tail -1 || true)
MAILCOW_BACKUP_LOCATION="$BACKUP_DIR" THREADS=2 \
  "$MAILCOW_DIR/helper-scripts/backup_and_restore.sh" backup all --delete-days "$LOCAL_KEEP_DAYS"
latest=$(ls -1d "$BACKUP_DIR"/mailcow-* | sort | tail -1)
[ -n "$latest" ] && [ "$latest" != "$before" ] || { log "ERROR: no new mailcow backup folder"; exit 1; }

# The helper leaves the folder and archives readable by everyone; lock them down.
chmod -R go-rwx "$latest"

log "extras -> $latest/extras"
mkdir -p "$latest/extras"
fail=0
# Registration database (consistent online copy) + its .env. A missing
# database is a failure, not a silent skip, unless SKIP_REGISTRATION=1.
if [ "$SKIP_REGISTRATION" != 1 ]; then
  if docker compose -f "$REG_COMPOSE" exec -T registration python -m app.cli backup /data/backup.sqlite3 \
     && docker compose -f "$REG_COMPOSE" cp registration:/data/backup.sqlite3 "$latest/extras/registration.sqlite3"; then
    docker compose -f "$REG_COMPOSE" exec -T registration rm -f /data/backup.sqlite3 || true
  else
    echo "  FAILED registration database backup (is the registration container running?)"; fail=1
  fi
  cp -a "$REPO_DIR/infrastructure/registration/.env" "$latest/extras/registration.env" \
    || { echo "  FAILED copying infrastructure/registration/.env"; fail=1; }
  cp -a "$REPO_DIR/infrastructure/registration/broker.env" "$latest/extras/broker.env" \
    || { echo "  FAILED copying infrastructure/registration/broker.env"; fail=1; }
fi
# Our customisations inside mailcow (nginx site, SOGo theme/logos, login page
# stylesheet, compose override) and mailcow.conf. Optional files are only
# included if present; anything else going wrong is a failure.
custom=()
for f in mailcow.conf docker-compose.override.yml data/conf/nginx data/conf/sogo \
         data/web/css/build/0081-custom-mailcow.css data/assets/ssl; do
  [ -e "$MAILCOW_DIR/$f" ] && custom+=("$f")
done
tar -C "$MAILCOW_DIR" -czf "$latest/extras/mailcow-custom.tgz" "${custom[@]}" \
  || { echo "  FAILED mailcow-custom.tgz"; fail=1; }
chmod -R go-rwx "$latest/extras"

log "verify"
for f in "$latest"/*.tar.zst; do
  if zstd -tq "$f"; then echo "  ok $(basename "$f") $(du -h "$f" | cut -f1)"; else echo "  CORRUPT $f"; fail=1; fi
done
need_files="backup_vmail.tar.zst backup_crypt.tar.zst backup_redis.tar.zst backup_mariadb.tar.zst mailcow.conf extras/mailcow-custom.tgz"
[ "$SKIP_REGISTRATION" = 1 ] || need_files="$need_files extras/registration.sqlite3 extras/registration.env extras/broker.env"
for need in $need_files; do
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
  if [ "$remote_type" = crypt ]; then
    rclone cryptcheck "$latest" "$RCLONE_REMOTE/$(basename "$latest")" --one-way
  else
    rclone check "$latest" "$RCLONE_REMOTE/$(basename "$latest")" --one-way
  fi
  log "remote retention: delete folders older than $REMOTE_KEEP_DAYS days"
  rclone delete "$RCLONE_REMOTE" --min-age "${REMOTE_KEEP_DAYS}d" && rclone rmdirs "$RCLONE_REMOTE" --leave-root
else
  log "WARNING: RCLONE_REMOTE not set; backup is only on this server"
fi
# Read by health-check.sh to alert on a stale backup.
mkdir -p "$BACKUP_ROOT"
echo "$(date -u +%s) $latest ${RCLONE_REMOTE:-local-only}" > "$BACKUP_ROOT/last-success"
log "done: $latest"
hc_ping "$HC_BACKUP_URL" success "$(tail -n 30 "$RUN_LOG")"
