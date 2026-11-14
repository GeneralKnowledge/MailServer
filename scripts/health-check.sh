#!/usr/bin/env bash
# Quick health check. Exit code 0 = healthy, 1 = something needs attention.
# Usage: scripts/health-check.sh [mail.example.com]     (run on the server)
# Cron example (mail root on failure):
#   */15 * * * * root /opt/MailServer/scripts/health-check.sh >/var/log/mail-health.log 2>&1 || mail -s "mail health" root </var/log/mail-health.log
set -uo pipefail
MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
HOST="${1:-$(sed -n 's/^MAILCOW_HOSTNAME=//p' "$MAILCOW_DIR/mailcow.conf" 2>/dev/null)}"
BASE_PATH="${BASE_PATH:-/join}"
problems=0
bad() { echo "PROBLEM: $*"; problems=$((problems+1)); }
ok() { echo "ok: $*"; }

echo "== Containers"
cd "$MAILCOW_DIR" 2>/dev/null || bad "mailcow dir $MAILCOW_DIR missing"
for project in mailcowdockerized registration; do
  while read -r name status; do
    [ -z "$name" ] && continue
    case "$status" in
      Up*unhealthy*|Up*starting*) bad "$name: $status" ;;
      Up*) ;;
      *) bad "$name: $status" ;;
    esac
  done < <(docker ps -a --filter "label=com.docker.compose.project=$project" --format '{{.Names}} {{.Status}}')
done
count=$(docker ps --filter "label=com.docker.compose.project=mailcowdockerized" -q | wc -l)
[ "$count" -ge 15 ] && ok "$count mailcow containers running" || bad "only $count mailcow containers running"

echo "== Ports and TLS on $HOST"
for p in 25 465 587 993 443; do
  if timeout 5 bash -c "exec 3<>/dev/tcp/$HOST/$p" 2>/dev/null; then ok "port $p open"; else bad "port $p not reachable"; fi
done
end=$(echo | timeout 10 openssl s_client -connect "$HOST:443" -servername "$HOST" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
if [ -n "$end" ]; then
  days=$(( ( $(date -d "$end" +%s) - $(date +%s) ) / 86400 ))
  [ "$days" -ge 14 ] && ok "certificate valid for $days more days" || bad "certificate expires in $days days"
else
  bad "could not read TLS certificate on $HOST:443"
fi

echo "== Registration app"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$HOST$BASE_PATH/healthz")
[ "$code" = 200 ] && ok "registration /healthz" || bad "registration /healthz returned $code"

echo "== Mail queue"
q=$(docker compose exec -T postfix-mailcow postqueue -j 2>/dev/null | wc -l)
[ "$q" -lt 50 ] && ok "$q messages queued" || bad "$q messages queued (check delivery)"

echo "== Resources"
used=$(df -P / | awk 'NR==2 {print $5+0}')
[ "$used" -lt 85 ] && ok "disk ${used}% used" || bad "disk ${used}% used"
free -m | awk '/^Mem:/ {printf "ok: memory %d/%d MB used\n", $3, $2}'
uptime

echo
[ "$problems" -eq 0 ] && { echo "HEALTHY"; exit 0; } || { echo "$problems problem(s)"; exit 1; }
