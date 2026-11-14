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

case "${1:-}" in
  list)
    rclone lsf "${RCLONE_REMOTE:?set RCLONE_REMOTE}" --dirs-only ;;
  fetch)
    name="${2:?backup folder name, see: restore.sh list}"
    mkdir -p "$RESTORE_DIR" && chmod 1777 "$RESTORE_DIR"
    rclone copy "${RCLONE_REMOTE:?}/$name" "$RESTORE_DIR/$name" --transfers 4
    (cd "$RESTORE_DIR/$name" && sha256sum -c --quiet SHA256SUMS) && echo "checksums ok: $RESTORE_DIR/$name" ;;
  mailcow)
    echo "mailcow's restore is interactive: pick the folder under $RESTORE_DIR and the components (normally all)."
    MAILCOW_BACKUP_LOCATION="$RESTORE_DIR" "$MAILCOW_DIR/helper-scripts/backup_and_restore.sh" restore ;;
  registration)
    src="$RESTORE_DIR/${2:?backup folder name}/extras"
    [ -f "$src/registration.sqlite3" ] || { echo "no registration.sqlite3 in $src"; exit 1; }
    [ -f "$REPO_DIR/infrastructure/registration/.env" ] || cp "$src/registration.env" "$REPO_DIR/infrastructure/registration/.env"
    $COMPOSE stop registration
    # Copy as the app user, replacing the live DB and its WAL files.
    $COMPOSE run --rm --no-deps -v "$src:/restore:ro" registration sh -c \
      'rm -f /data/registration.sqlite3-wal /data/registration.sqlite3-shm && cp /restore/registration.sqlite3 /data/registration.sqlite3'
    $COMPOSE start registration
    $COMPOSE exec -T registration python -m app.cli list | head ;;
  *)
    sed -n '2,7p' "$0"; exit 1 ;;
esac
