#!/usr/bin/env bash
# Daily reconciliation (report only): compares the mailboxes the control plane
# created with what mailcow has, records a reconcile.report audit event, and
# sends one alert if something we created is missing or the check can't run.
# It changes nothing. Details: broker/broker/reconcile.py.
# Cron (04:10 daily, after the backup):
#   10 4 * * * root /opt/MailServer/scripts/reconcile.sh >>/var/log/mail-reconcile.log 2>&1
set -uo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
# shellcheck source=scripts/lib/alerts.sh
. "$REPO_DIR/scripts/lib/alerts.sh"

echo "== $(date -u +%FT%TZ) reconcile"
out=$(docker compose -f "$REPO_DIR/infrastructure/registration/docker-compose.yml" exec -T broker \
        python -m broker.reconcile 2>&1)
rc=$?
echo "$out"
case "$rc" in
  0) ;;
  1) alert_send high "mailbox drift" "$(echo "$out" | grep DRIFT | head -n 20)" ;;
  *) alert_send high "reconciliation failed" "$(echo "$out" | tail -n 3)" ;;
esac
exit "$rc"
