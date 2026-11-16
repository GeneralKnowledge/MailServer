#!/usr/bin/env bash
# Daily deletion job (PR 16, ADR 0021): removes accounts whose deletion is
# due, only after the grace period and only once a backup that started after
# the deletion was scheduled has completed. Details: broker/broker/purge.py.
# Sends one alert if a deletion failed or was stopped, or the job can't run.
#   scripts/purge-deleted.sh --dry-run    # say what would be deleted
# Cron (05:10 daily, after the backup and the reconciliation):
#   10 5 * * * root /opt/MailServer/scripts/purge-deleted.sh >>/var/log/mail-purge.log 2>&1
set -uo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
# shellcheck source=scripts/lib/alerts.sh
. "$REPO_DIR/scripts/lib/alerts.sh"

echo "== $(date -u +%FT%TZ) purge $*"
out=$(docker compose -f "$REPO_DIR/infrastructure/registration/docker-compose.yml" exec -T broker \
        python -m broker.purge "$@" 2>&1)
rc=$?
echo "$out"
case "$rc" in
  0) ;;
  1) alert_send high "account deletion needs attention" "$(echo "$out" | grep -E 'FAILED|STOPPED' | head -n 20)" ;;
  *) alert_send high "deletion job failed" "$(echo "$out" | tail -n 3)" ;;
esac
exit "$rc"
