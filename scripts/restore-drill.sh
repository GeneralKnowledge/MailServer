#!/usr/bin/env bash
# Restore drill (PR 29): proves a backup can be restored, without touching
# anything that is running. It restores the parts we own into throwaway
# targets and reads the mail archives end to end:
#
#   - checksums (SHA256SUMS) of the whole folder;
#   - every mailcow archive decompresses and lists to the end; the mail archive
#     holds messages, the crypt archive holds the mail-crypt keys (mail is
#     unreadable without them);
#   - the control-plane database opens with the *current* broker code, in a
#     throwaway container without network, and its audit log is still append-only;
#   - the invitation database passes integrity_check;
#   - Keycloak's dump restores into a throwaway Postgres without network and
#     holds the avedeus realm, its users and clients;
#   - the secrets a restore needs are in the backup.
#
# Not done here (needs a second server; see docs/backup-and-recovery.md):
# restoring mailcow itself (MariaDB, Redis, vmail) and starting Keycloak on
# the restored database. The full wipe-and-restore stays a manual exercise.
#
#   scripts/restore-drill.sh                  # newest local backup
#   scripts/restore-drill.sh --remote         # newest off-site backup (downloaded to a temp folder)
#   scripts/restore-drill.sh /var/backups/mailserver/mailcow/mailcow-YYYY-...
# Cron (monthly, the 1st at 06:00, after the backup):
#   0 6 1 * * root /opt/MailServer/scripts/restore-drill.sh --remote >>/var/log/mail-drill.log 2>&1
# Exit 0: the drill passed. 1: something failed (alert sent). 2: could not run.
set -uo pipefail
umask 077
# shellcheck disable=SC1091
[ -f /etc/mailserver/backup.env ] && . /etc/mailserver/backup.env
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/mailserver}"
BACKUP_DIR="${BACKUP_DIR:-$BACKUP_ROOT/mailcow}"
BROKER_IMAGE="${BROKER_IMAGE:-mailserver-broker:local}"
PG_IMAGE="${PG_IMAGE:-$(sed -n 's/^ *image: *\(postgres:[^ ]*\).*/\1/p' "$REPO_DIR/infrastructure/identity/docker-compose.yml" | head -n 1)}"
MAX_AGE_HOURS="${DRILL_MAX_AGE_HOURS:-48}"
# shellcheck source=scripts/lib/alerts.sh
. "$REPO_DIR/scripts/lib/alerts.sh"

fail=0
ok()  { echo "  ok     $*"; }
bad() { echo "  FAILED $*"; fail=1; }
note() { echo "  note   $*"; }

mkdir -p "$BACKUP_ROOT" && chmod 700 "$BACKUP_ROOT"
WORK=$(mktemp -d "$BACKUP_ROOT/drill.XXXXXX") || { echo "drill: cannot create a work folder"; exit 2; }
PG="avedeus-drill-pg-$$"
cleanup() { docker rm -f "$PG" >/dev/null 2>&1; rm -rf "$WORK"; }
trap cleanup EXIT

echo "== $(date -u +%FT%TZ) restore drill $*"
case "${1:-}" in
  --remote)
    : "${RCLONE_REMOTE:?set RCLONE_REMOTE in /etc/mailserver/backup.env}"
    name=$(rclone lsf "$RCLONE_REMOTE" --dirs-only 2>/dev/null | sed 's#/$##' | grep '^mailcow-' | sort | tail -n 1)
    [ -n "$name" ] || { echo "drill: no backups found at $RCLONE_REMOTE"; exit 2; }
    echo "fetching $name from $RCLONE_REMOTE"
    rclone copy "$RCLONE_REMOTE/$name" "$WORK/$name" --transfers 4 || { echo "drill: download failed"; exit 2; }
    folder="$WORK/$name" ;;
  "")
    folder=$(find "$BACKUP_DIR" -maxdepth 1 -type d -name 'mailcow-*' 2>/dev/null | sort | tail -n 1)
    [ -n "$folder" ] || { echo "drill: no backups in $BACKUP_DIR"; exit 2; }
    # Never work on the backup itself: copy what the drill opens.
    ;;
  *) folder="$1"; [ -d "$folder" ] || { echo "drill: no such folder $folder"; exit 2; } ;;
esac
echo "backup: $folder"

# Age: a drill of a stale backup proves little.
age_h=$(( ($(date +%s) - $(stat -c %Y "$folder")) / 3600 ))
if [ "$age_h" -le "$MAX_AGE_HOURS" ]; then ok "backup is ${age_h} h old"; else bad "backup is ${age_h} h old (more than $MAX_AGE_HOURS h)"; fi

# 1. Checksums.
if [ -s "$folder/SHA256SUMS" ] && (cd "$folder" && sha256sum -c --quiet SHA256SUMS); then
  ok "checksums ($(wc -l < "$folder/SHA256SUMS") files)"
else
  bad "checksums do not match (or SHA256SUMS is missing); do not restore from this backup"
fi

# 2. mailcow archives: decompress and list to the end.
list() { zstd -dcq "$1" | tar -t 2>/dev/null; }
for part in vmail crypt mariadb redis; do
  f="$folder/backup_$part.tar.zst"
  if [ ! -s "$f" ]; then bad "backup_$part.tar.zst missing"; continue; fi
  if ! entries=$(list "$f" | wc -l) || [ "$entries" -eq 0 ]; then bad "backup_$part.tar.zst does not list"; continue; fi
  case "$part" in
    vmail) msgs=$(list "$f" | grep -cE '/(cur|new)/[^/]+$' || true)
           if [ "$msgs" -gt 0 ]; then ok "mail archive lists $entries entries, $msgs messages"
           else bad "mail archive holds no messages ($entries entries)"; fi ;;
    crypt) if list "$f" | grep -q 'ecprivkey.pem'; then ok "crypt archive holds the mail-crypt private key"
           else bad "crypt archive has no ecprivkey.pem: the mail could not be decrypted"; fi ;;
    *)     ok "backup_$part.tar.zst lists $entries entries" ;;
  esac
done

# 3. Secrets a restore needs.
for f in mailcow.conf extras/mailcow-custom.tgz; do
  [ -s "$folder/$f" ] && ok "$f present" || bad "$f missing"
done
for f in extras/registration.env extras/broker.env; do
  [ -s "$folder/$f" ] && ok "$f present" || note "$f not in this backup (SKIP_REGISTRATION?)"
done

# 4. Control-plane database, opened by the current broker code.
if [ -s "$folder/extras/control-plane.sqlite3" ]; then
  mkdir -p "$WORK/cp" && cp "$folder/extras/control-plane.sqlite3" "$WORK/cp/control-plane.sqlite3"
  chmod 777 "$WORK/cp" && chmod 666 "$WORK/cp/control-plane.sqlite3"   # the image's own user writes the copy
  if docker image inspect "$BROKER_IMAGE" >/dev/null 2>&1; then
    if out=$(docker run --rm --network none -v "$WORK/cp:/data" "$BROKER_IMAGE" python -c '
import sqlite3
from broker.store import Store
s = Store("/data/control-plane.sqlite3")
assert s.db.execute("pragma integrity_check").fetchone()[0] == "ok"
n = s.db.execute("select count(*) from audit_events").fetchone()[0]
try:
    s.db.execute("update audit_events set outcome = outcome")
    raise SystemExit("audit log accepted an UPDATE")
except sqlite3.DatabaseError:
    pass
links = s.db.execute("select count(*) from identity_links").fetchone()[0]
dels = len(s.deletions())
last = (s.latest("backup.completed") or {}).get("time", "none")
print(n, "audit events,", links, "identity links,", dels, "scheduled deletions, last recorded backup", last)
' 2>&1); then ok "control-plane database opens with the current broker: $out"
    else bad "control-plane database: $(echo "$out" | tail -n 1)"; fi
  else
    if python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); assert c.execute('pragma integrity_check').fetchone()[0]=='ok'" \
         "$WORK/cp/control-plane.sqlite3"; then
      ok "control-plane database integrity (image $BROKER_IMAGE not built here, so not opened with the broker code)"
    else bad "control-plane database integrity_check"; fi
  fi
else
  note "no control-plane database in this backup"
fi

# 5. Invitation database.
if [ -s "$folder/extras/registration.sqlite3" ]; then
  if out=$(python3 -c "import sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro', uri=True); assert c.execute('pragma integrity_check').fetchone()[0]=='ok'; print(c.execute('select count(*) from invitations').fetchone()[0])" \
             "$folder/extras/registration.sqlite3" 2>&1); then ok "invitation database: $out invitations"
  else bad "invitation database: $(echo "$out" | tail -n 1)"; fi
fi

# 6. Keycloak: restore the dump into a throwaway Postgres without network.
if [ -s "$folder/extras/keycloak.pgdump" ]; then
  [ -s "$folder/extras/identity.env" ] && ok "extras/identity.env present" || bad "extras/identity.env missing"
  if [ -z "$PG_IMAGE" ]; then
    bad "cannot tell which Postgres image the identity stack uses"
  elif docker run -d --name "$PG" --network none -e POSTGRES_USER=keycloak -e POSTGRES_DB=keycloak \
         -e POSTGRES_PASSWORD="drill-$RANDOM$RANDOM" "$PG_IMAGE" >/dev/null; then
    ready=0
    for _ in $(seq 1 60); do
      docker exec "$PG" pg_isready -U keycloak -d keycloak >/dev/null 2>&1 && { ready=1; break; }; sleep 1
    done
    sleep 2   # the image restarts Postgres once after its first-start setup
    for _ in $(seq 1 30); do docker exec "$PG" pg_isready -U keycloak -d keycloak >/dev/null 2>&1 && break; sleep 1; done
    if [ "$ready" = 1 ] && docker exec -i "$PG" pg_restore -U keycloak -d keycloak --no-owner --exit-on-error \
         < "$folder/extras/keycloak.pgdump" 2>"$WORK/pg.err"; then
      q() { docker exec "$PG" psql -U keycloak -d keycloak -tAc "$1" 2>/dev/null; }
      realm=$(q "select id from realm where name = 'avedeus'")
      if [ -n "$realm" ]; then
        ok "Keycloak dump restores: realm avedeus, $(q "select count(*) from user_entity where realm_id = '$realm' and service_account_client_link is null") people, $(q "select count(*) from client where realm_id = '$realm'") clients"
      else
        bad "Keycloak dump restored but has no avedeus realm"
      fi
    else
      bad "Keycloak dump does not restore: $(tail -n 1 "$WORK/pg.err" 2>/dev/null)"
    fi
  else
    bad "could not start a throwaway Postgres ($PG_IMAGE)"
  fi
else
  note "no Keycloak dump in this backup (Keycloak not deployed)"
fi

result=$([ "$fail" = 0 ] && echo passed || echo failed)
echo "$(date -u +%s) $(basename "$folder") $result" > "$BACKUP_ROOT/last-drill"
echo "restore drill $result"
if [ "$fail" != 0 ]; then
  alert_send high "restore drill failed" "$(basename "$folder"): see /var/log/mail-drill.log"
  exit 1
fi
exit 0
