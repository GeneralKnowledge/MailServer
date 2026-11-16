#!/usr/bin/env bash
# CI only (PR 29): builds a small synthetic backup folder in the layout
# backup.sh writes, with a real Keycloak dump from the running identity stack,
# so scripts/restore-drill.sh can be run against it.
#   scripts/ci/drill-fixture.sh BACKUP_ROOT
set -euo pipefail
ROOT="${1:?backup root}"
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
F="$ROOT/mailcow/mailcow-$(date -u +%Y-%m-%d-%H-%M-%S)"
S=$(mktemp -d)
mkdir -p "$F/extras" "$S/vmail/example.com/alice/Maildir/cur" "$S/crypt" "$S/db"
printf 'Subject: drill\n\nhello\n' > "$S/vmail/example.com/alice/Maildir/cur/1.eml"
echo key > "$S/crypt/ecprivkey.pem"
echo data > "$S/db/x"
for part in vmail crypt; do tar -C "$S" -c "$part" | zstd -q > "$F/backup_$part.tar.zst"; done
for part in mariadb redis; do tar -C "$S" -c db | zstd -q > "$F/backup_$part.tar.zst"; done
echo "MAILCOW_HOSTNAME=mx.example.test" > "$F/mailcow.conf"
tar -C "$S" -czf "$F/extras/mailcow-custom.tgz" db
for e in registration broker identity; do echo "X=1" > "$F/extras/$e.env"; done
PYTHONPATH="$REPO_DIR/broker" python3 - "$F/extras" <<'PY'
import sqlite3, sys
from broker.store import Store
d = sys.argv[1]
s = Store(f"{d}/control-plane.sqlite3")
s.audit(caller="signup", action="account.create", outcome="ok", address="alice@example.com")
s.link_set("alice@example.com", "keycloak", "u-1")
s.db.execute("pragma journal_mode=delete")
c = sqlite3.connect(f"{d}/registration.sqlite3")
c.execute("create table invitations (id integer primary key)")
c.execute("insert into invitations values (1)")
c.commit()
PY
rm -f "$F"/extras/*-wal "$F"/extras/*-shm
docker compose -f "$REPO_DIR/infrastructure/identity/docker-compose.yml" \
  --env-file "$REPO_DIR/infrastructure/identity/identity.env" exec -T postgres \
  pg_dump -U keycloak -d keycloak -Fc > "$F/extras/keycloak.pgdump"
(cd "$F" && find . -type f ! -name SHA256SUMS -print0 | xargs -0 sha256sum > SHA256SUMS)
rm -rf "$S"
echo "$F"
