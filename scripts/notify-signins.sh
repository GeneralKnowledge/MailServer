#!/usr/bin/env bash
# Sign-in notifications (PR 26): mails people about a sign-in to their account
# from a network it hasn't been used from before. Details: broker/broker/notify.py.
# Does nothing unless NOTIFY_SIGNINS=true in broker.env.
# Alerts once when it starts failing and once when it recovers, not every run.
#   scripts/notify-signins.sh --dry-run    # say who would be mailed
# Cron (every 10 minutes):
#   */10 * * * * root /opt/MailServer/scripts/notify-signins.sh >>/var/log/mail-notify.log 2>&1
set -uo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
# shellcheck source=scripts/lib/alerts.sh
. "$REPO_DIR/scripts/lib/alerts.sh"

echo "== $(date -u +%FT%TZ) notify $*"
out=$(docker compose -f "$REPO_DIR/infrastructure/registration/docker-compose.yml" exec -T broker \
        python -m broker.notify "$@" 2>&1)
rc=$?
echo "$out"
STATE="${NOTIFY_STATE_FILE:-/var/tmp/mail-notify.state}"
prev=$(cat "$STATE" 2>/dev/null || echo 0)
if [ "$rc" != 0 ] && [ "$prev" = 0 ]; then
  alert_send high "sign-in notifications failing" "$(echo "$out" | grep -E 'FAILED|unavailable' | head -n 5)"
elif [ "$rc" = 0 ] && [ "$prev" != 0 ]; then
  alert_send default "sign-in notifications working again" "$(echo "$out" | tail -n 1)"
fi
echo "$rc" > "$STATE" 2>/dev/null || true
exit "$rc"
