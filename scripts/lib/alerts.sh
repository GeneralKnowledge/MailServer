# shellcheck shell=bash
# Alert helpers, sourced by backup.sh and health-check.sh.
#
# Config: /etc/mailserver/alerts.env (chmod 600), all optional:
#   NTFY_URL=https://ntfy.sh/<long-random-topic>  # push to your phone (ntfy app)
#   NTFY_TOKEN=                                    # if the topic needs an access token
#   ALERT_EMAIL=you@example.net                    # ntfy also emails this address. It goes
#                                                  # out through ntfy, not this server, so it
#                                                  # still arrives when the mail server is down.
#   HC_BACKUP_URL=https://hc-ping.com/<uuid>       # healthchecks.io check for the daily backup
#   HC_HEALTH_URL=https://hc-ping.com/<uuid>       # healthchecks.io check for the 15-min health run
#
# healthchecks.io alerts when a ping is late, so a dead server, a stopped cron
# or a hung script is noticed too. Nothing here ever makes the caller fail.

ALERTS_ENV="${ALERTS_ENV:-/etc/mailserver/alerts.env}"
# shellcheck disable=SC1090
[ -f "$ALERTS_ENV" ] && . "$ALERTS_ENV"
NTFY_URL="${NTFY_URL:-}"
NTFY_TOKEN="${NTFY_TOKEN:-}"
ALERT_EMAIL="${ALERT_EMAIL:-}"
ALERT_HOST="${ALERT_HOST:-$(hostname -f 2>/dev/null || hostname)}"

# alert_send PRIORITY TITLE MESSAGE   (PRIORITY: high = problem, default = recovered/info)
alert_send() {
  local prio="$1" title="$2" msg="$3"
  if [ -z "$NTFY_URL" ]; then
    echo "ALERT (not sent, NTFY_URL unset): $title: $msg" >&2
    return 0
  fi
  local args=(-fsS -m 15 --retry 3 -o /dev/null -H "Title: $ALERT_HOST: $title" -H "Priority: $prio"
              -H "Tags: $([ "$prio" = high ] && echo rotating_light || echo white_check_mark)")
  [ -n "$NTFY_TOKEN" ] && args+=(-H "Authorization: Bearer $NTFY_TOKEN")
  [ -n "$ALERT_EMAIL" ] && args+=(-H "Email: $ALERT_EMAIL")
  curl "${args[@]}" --data-binary @- "$NTFY_URL" <<<"$msg" || echo "WARNING: could not send alert to ntfy" >&2
  return 0
}

# hc_ping URL start|success|fail [BODY]
hc_ping() {
  local url="$1" what="$2" body="${3:-}"
  [ -n "$url" ] || return 0
  case "$what" in
    start) url="$url/start" ;;
    fail) url="$url/fail" ;;
  esac
  curl -fsS -m 15 --retry 3 -o /dev/null --data-binary @- "$url" <<<"${body: -10000}" \
    || echo "WARNING: could not ping healthchecks" >&2
  return 0
}
