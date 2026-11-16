#!/usr/bin/env bash
# Restore helper. Steps (full procedure: docs/backup-and-recovery.md):
#   1. scripts/restore.sh fetch <backup-folder-name>   download from off-site to /var/backups/mailserver/restore
#   2. scripts/restore.sh mailcow                       run mailcow's interactive restore (choose that folder)
#   3. scripts/restore.sh registration <backup-folder>  restore the invitation and control-plane databases
#   4. scripts/restore.sh identity <backup-folder>      restore Keycloak's database (only once Keycloak is deployed)
#   5. scripts/restore.sh list                          list remote backups
set -euo pipefail
umask 077  # everything this script writes holds mail or secrets
[ -f /etc/mailserver/backup.env ] && . /etc/mailserver/backup.env
MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/mailserver}"
RESTORE_DIR="${RESTORE_DIR:-$BACKUP_ROOT/restore}"
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
    # Root-only from the start: the download holds all mail and every secret.
    mkdir -p "$RESTORE_DIR"
    case "$RESTORE_DIR" in "$BACKUP_ROOT"/*) chmod 700 "$BACKUP_ROOT" ;; esac
    chmod 700 "$RESTORE_DIR"
    rclone copy "${RCLONE_REMOTE:?}/$name" "$RESTORE_DIR/$name" --transfers 4
    chmod -R go-rwx "$RESTORE_DIR/$name"
    if ! (cd "$RESTORE_DIR/$name" && sha256sum -c --quiet SHA256SUMS); then
      echo "ERROR: checksum verification failed for $RESTORE_DIR/$name; do not restore from it." >&2
      exit 1
    fi
    echo "checksums ok: $RESTORE_DIR/$name" ;;
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
    if [ ! -f "$REPO_DIR/infrastructure/registration/broker.env" ] && [ -f "$src/broker.env" ]; then
      cp "$src/broker.env" "$REPO_DIR/infrastructure/registration/broker.env"
    fi
    $COMPOSE stop registration
    # Keep the current database next to the restore, in case it was the newer one.
    keep="$RESTORE_DIR/registration-before-restore-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
    if $COMPOSE run --rm --no-deps -T registration sh -c \
         'test -f /data/registration.sqlite3 && python -m app.cli backup /tmp/prev.sqlite3 >/dev/null && cat /tmp/prev.sqlite3' \
         > "$keep"; then
      chmod 600 "$keep"; echo "previous database saved to $keep"
    else
      rm -f "$keep"
    fi
    # Stream the file in (the backup is root-only) and write it as the app user,
    # replacing the live DB and its WAL files.
    $COMPOSE run --rm --no-deps -T registration sh -c \
      'rm -f /data/registration.sqlite3-wal /data/registration.sqlite3-shm && cat > /data/registration.sqlite3' \
      < "$src/registration.sqlite3"
    $COMPOSE up -d registration
    $COMPOSE exec -T registration python -m app.cli list | head
    # Control-plane database (audit log). Older backups (before PR 9) have none.
    if [ -f "$src/control-plane.sqlite3" ]; then
      $COMPOSE stop broker
      keep="$RESTORE_DIR/control-plane-before-restore-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
      if $COMPOSE run --rm --no-deps -T broker sh -c \
           'test -f /data/control-plane.sqlite3 && python -m broker.backup /tmp/prev.sqlite3 >/dev/null && cat /tmp/prev.sqlite3' \
           > "$keep" 2>/dev/null && [ -s "$keep" ]; then
        echo "previous control-plane database saved to $keep"
      else
        rm -f "$keep"
      fi
      $COMPOSE run --rm --no-deps -T broker sh -c \
        'rm -f /data/control-plane.sqlite3-wal /data/control-plane.sqlite3-shm && cat > /data/control-plane.sqlite3' \
        < "$src/control-plane.sqlite3"
      $COMPOSE up -d broker
      echo "control-plane database restored"
    else
      echo "no control-plane.sqlite3 in $src (backup older than PR 9); the broker keeps its current database"
    fi ;;
  identity)
    src="$RESTORE_DIR/${2:?backup folder name}/extras"
    ID="docker compose -f $REPO_DIR/infrastructure/identity/docker-compose.yml --env-file $REPO_DIR/infrastructure/identity/identity.env"
    [ -s "$src/keycloak.pgdump" ] || { echo "no keycloak.pgdump in $src"; exit 1; }
    [ -f "$REPO_DIR/infrastructure/identity/identity.env" ] || cp "$src/identity.env" "$REPO_DIR/infrastructure/identity/identity.env"
    $ID stop keycloak
    $ID up -d postgres
    for _ in $(seq 1 30); do $ID exec -T postgres pg_isready -U keycloak -d postgres >/dev/null 2>&1 && break; sleep 2; done
    # Keep the current database next to the restore, in case it was the newer one.
    keep="$RESTORE_DIR/keycloak-before-restore-$(date -u +%Y%m%dT%H%M%SZ).pgdump"
    if $ID exec -T postgres pg_dump -U keycloak -d keycloak -Fc > "$keep" 2>/dev/null && [ -s "$keep" ]; then
      echo "previous Keycloak database saved to $keep"
    else
      rm -f "$keep"
    fi
    $ID exec -T postgres dropdb -U keycloak --maintenance-db=postgres --if-exists keycloak
    $ID exec -T postgres createdb -U keycloak --maintenance-db=postgres -O keycloak keycloak
    $ID exec -T postgres pg_restore -U keycloak -d keycloak --no-owner --exit-on-error < "$src/keycloak.pgdump"
    $ID up -d keycloak
    echo "Keycloak database restored; Keycloak is starting (allow about two minutes)" ;;
  *)
    sed -n '2,8p' "$0"; exit 1 ;;
esac
