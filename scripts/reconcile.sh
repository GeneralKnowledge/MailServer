#!/usr/bin/env bash
# Daily reconciliation: compares what mailcow, Keycloak and the control plane's
# identity links say (PR 9, PR 31), records a reconcile.report audit event, and
# sends one alert on drift or if the check can't run. Its one fix is recording
# a missing identity link when exactly one Keycloak user has the mailbox's
# address as email; nothing in mailcow or Keycloak is changed.
#   scripts/reconcile.sh --dry-run    # report only, record and fix nothing
# Details: broker/broker/reconcile.py.
# Cron (04:10 daily, after the backup):
#   10 4 * * * root /opt/MailServer/scripts/reconcile.sh >>/var/log/mail-reconcile.log 2>&1
set -uo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
# shellcheck source=scripts/lib/alerts.sh
. "$REPO_DIR/scripts/lib/alerts.sh"

echo "== $(date -u +%FT%TZ) reconcile $*"
out=$(docker compose -f "$REPO_DIR/infrastructure/registration/docker-compose.yml" exec -T broker \
        python -m broker.reconcile "$@" 2>&1)
rc=$?
echo "$out"
case "$rc" in
  0) ;;
  1) alert_send high "account drift" "$(echo "$out" | grep DRIFT | head -n 20)" ;;
  *) alert_send high "reconciliation failed" "$(echo "$out" | tail -n 3)" ;;
esac
exit "$rc"
