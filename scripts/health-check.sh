#!/usr/bin/env bash
# Health check. Exit code 0 = healthy, 1 = something needs attention.
# Usage: scripts/health-check.sh [mail.example.com]     (run on the server, as root)
#
# Alerts (see scripts/lib/alerts.sh, config in /etc/mailserver/alerts.env):
#   - one push alert when the set of problems changes, and one when all clear;
#     not every 15 minutes while a problem lasts
#   - every run pings HC_HEALTH_URL (or HC_HEALTH_URL/fail), so healthchecks.io
#     notices when the server or this cron job stops
# Cron (every 15 minutes):
#   */15 * * * * root /opt/MailServer/scripts/health-check.sh >/var/log/mail-health.log 2>&1
set -uo pipefail
MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
HOST="${1:-$(sed -n 's/^MAILCOW_HOSTNAME=//p' "$MAILCOW_DIR/mailcow.conf" 2>/dev/null)}"
BASE_PATH="${BASE_PATH:-/join}"
[ -f /etc/mailserver/backup.env ] && . /etc/mailserver/backup.env
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/mailserver}"
BACKUP_MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"
STATE_DIR="${STATE_DIR:-/var/lib/mailserver}"
# shellcheck source=lib/alerts.sh
. "$REPO_DIR/scripts/lib/alerts.sh"
HC_HEALTH_URL="${HC_HEALTH_URL:-}"

problem_list=()   # messages, for the alert text
problem_keys=()   # stable names, so a changing number (queue size, age) doesn't re-alert
bad() { local key="$1"; shift; echo "PROBLEM: $*"; problem_keys+=("$key"); problem_list+=("$*"); }
ok() { echo "ok: $*"; }

echo "== Containers"
cd "$MAILCOW_DIR" 2>/dev/null || bad mailcow-dir "mailcow dir $MAILCOW_DIR missing"
for project in mailcowdockerized registration; do
  while read -r name status; do
    [ -z "$name" ] && continue
    case "$status" in
      Up*unhealthy*|Up*starting*) bad "container:$name" "$name: $status" ;;
      Up*) ;;
      *) bad "container:$name" "$name: $status" ;;
    esac
  done < <(docker ps -a --filter "label=com.docker.compose.project=$project" --format '{{.Names}} {{.Status}}')
done
count=$(docker ps --filter "label=com.docker.compose.project=mailcowdockerized" -q | wc -l)
if [ "$count" -ge 15 ]; then ok "$count mailcow containers running"; else bad container-count "only $count mailcow containers running"; fi

echo "== Ports and TLS on $HOST"
for p in 25 465 587 993 443; do
  if timeout 5 bash -c "exec 3<>/dev/tcp/$HOST/$p" 2>/dev/null; then ok "port $p open"; else bad "port:$p" "port $p not reachable"; fi
done
end=$(echo | timeout 10 openssl s_client -connect "$HOST:443" -servername "$HOST" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
if [ -n "$end" ]; then
  days=$(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 ))
  if [ "$days" -ge 14 ]; then ok "certificate valid for $days more days"; else bad cert-expiry "certificate expires in $days days"; fi
else
  bad cert-read "could not read TLS certificate on $HOST:443"
fi

echo "== Registration app"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$HOST$BASE_PATH/healthz")
if [ "$code" = 200 ]; then ok "registration /healthz"; else bad registration "registration /healthz returned $code"; fi

echo "== Mail queue"
q=$(docker compose exec -T postfix-mailcow postqueue -j 2>/dev/null | wc -l)
if [ "$q" -lt 50 ]; then ok "$q messages queued"; else bad queue "$q messages queued (check delivery)"; fi

echo "== Backups"
if read -r stamp folder remote < "$BACKUP_ROOT/last-success" 2>/dev/null; then
  age_h=$(( ( $(date +%s) - stamp ) / 3600 ))
  if [ "$age_h" -le "$BACKUP_MAX_AGE_HOURS" ]; then ok "last good backup ${age_h} h ago ($(basename "$folder") -> $remote)"
  else bad backup-age "last good backup is ${age_h} h old (limit ${BACKUP_MAX_AGE_HOURS} h); see /var/log/mail-backup.log"; fi
  [ "$remote" != local-only ] || bad backup-offsite "backups are not copied off the server (RCLONE_REMOTE unset)"
else
  bad backup-none "no successful backup recorded in $BACKUP_ROOT/last-success"
fi

echo "== Resources"
used=$(df -P / | awk 'NR==2 {print $5+0}')
if [ "$used" -lt 85 ]; then ok "disk ${used}% used"; else bad disk "disk ${used}% used"; fi
free -m | awk '/^Mem:/ {printf "ok: memory %d/%d MB used\n", $3, $2}'
uptime

# ---- alerts: only when the set of problems changes ----------------------------
mkdir -p "$STATE_DIR"
state="$STATE_DIR/health.last"
current="$(printf '%s\n' "${problem_keys[@]+"${problem_keys[@]}"}" | sed '/^$/d' | sort -u)"
details="$(printf '%s\n' "${problem_list[@]+"${problem_list[@]}"}")"
previous="$(cat "$state" 2>/dev/null || true)"
if [ "$current" != "$previous" ]; then
  if [ -n "$current" ]; then
    alert_send high "${#problem_list[@]} problem(s)" "$details"
  else
    alert_send default "all clear" "Health check passes again."
  fi
  printf '%s' "$current" > "$state"
fi

echo
if [ "${#problem_list[@]}" -eq 0 ]; then
  hc_ping "$HC_HEALTH_URL" success "healthy"
  echo "HEALTHY"; exit 0
fi
hc_ping "$HC_HEALTH_URL" fail "$details"
echo "${#problem_list[@]} problem(s)"; exit 1
