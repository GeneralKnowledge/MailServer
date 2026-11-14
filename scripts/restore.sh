#!/usr/bin/env bash
# Restore helper. Steps (full procedure: docs/backup-and-recovery.md):
#   1. scripts/restore.sh fetch <backup-folder-name>   download from off-site to /var/backups/mailcow-restore
#   2. scripts/restore.sh mailcow                       run mailcow's interactive restore (choose that folder)
#   3. scripts/restore.sh registration <backup-folder>  restore the invitation database
#   4. scripts/restore.sh list                          list remote backups
set -euo pipefail
[ -f /etc/mailserver/backup.env ] && . /etc/mailserver/backup.env
MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
RESTORE_DIR="${RESTORE_DIR:-/var/backups/mailcow-restore}"
COMPOSE="docker compose -f $REPO_DIR/infrastructure/registration/docker-compose.yml"

# mailcow's generate_config.sh leaves DOCKER_COMPOSE_VERSION empty until the
# first update.sh, and backup_and_restore.sh's restore refuses to run then.
fix_compose_version() {
  local conf="$MAILCOW_DIR/mailcow.conf"
  if grep -q '^DOCKER_COMPOSE_VERSION=$' "$conf" 2>/dev/null && docker compose version >/dev/null 2>&1; then
    sed -i 's/^DOCKER_COMPOSE_VERSION=$/DOCKER_COMPOSE_VERSION=native/' "$conf"
    echo "Set DOCKER_COMPOSE_VERSION=native in $conf (was empty)"
  fi
}

case "${1:-}" in
  list)
    rclone lsf "${RCLONE_REMOTE:?set RCLONE_REMOTE}" --dirs-only ;;
  fetch)
    name="${2:?backup folder name, see: restore.sh list}"
    mkdir -p "$RESTORE_DIR" && chmod 1777 "$RESTORE_DIR"
    rclone copy "${RCLONE_REMOTE:?}/$name" "$RESTORE_DIR/$name" --transfers 4
    (cd "$RESTORE_DIR/$name" && sha256sum -c --quiet SHA256SUMS) && echo "checksums ok: $RESTORE_DIR/$name"
    # rclone doesn't keep permissions; the folder holds mailcow.conf and .env secrets.
    chmod -R go-rwx "$RESTORE_DIR/$name" ;;
  mailcow)
    fix_compose_version
    echo "mailcow's restore is interactive: pick the folder under $RESTORE_DIR and the components (normally all)."
    MAILCOW_BACKUP_LOCATION="$RESTORE_DIR" "$MAILCOW_DIR/helper-scripts/backup_and_restore.sh" restore
    # The helper restarts mailcow with docker-compose.yml only, so the
    # docker-compose.override.yml (SOGo theme mount) isn't applied. Re-apply it.
    (cd "$MAILCOW_DIR" && docker compose up -d) ;;
  registration)
    src="$RESTORE_DIR/${2:?backup folder name}/extras"
    [ -f "$src/registration.sqlite3" ] || { echo "no registration.sqlite3 in $src"; exit 1; }
    [ -f "$REPO_DIR/infrastructure/registration/.env" ] || cp "$src/registration.env" "$REPO_DIR/infrastructure/registration/.env"
    $COMPOSE stop registration
    # Stream the file in (the backup is root-only) and write it as the app user,
    # replacing the live DB and its WAL files.
    $COMPOSE run --rm --no-deps -T registration sh -c \
      'rm -f /data/registration.sqlite3-wal /data/registration.sqlite3-shm && cat > /data/registration.sqlite3' \
      < "$src/registration.sqlite3"
    $COMPOSE up -d registration
    $COMPOSE exec -T registration python -m app.cli list | head ;;
  *)
    sed -n '2,7p' "$0"; exit 1 ;;
esac
