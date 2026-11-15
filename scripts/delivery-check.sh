#!/usr/bin/env bash
# Daily delivery and DNS checks (PR 30): MX, A/AAAA, PTR, SPF, DKIM (compared
# with mailcow's key), DMARC; the server and the mail domain on DNS
# blocklists; and, if PROBE_TO is set, one probe message to an outside mailbox
# you own, which passes when the receiving server accepts it. Details:
# scripts/lib/mailcheck.py.
#
# Settings in /etc/mailserver/delivery.env (all but the first two optional):
#   MAIL_DOMAIN=mail.avedeus.ovh          # the addresses' domain
#   MAIL_HOST=mx.avedeus.ovh              # default: MAILCOW_HOSTNAME from mailcow.conf
#   PUBLIC_IPV4=203.0.113.10  PUBLIC_IPV6=2001:db8::10   # this server; default: the host's A record
#   RESOLVER=127.0.0.1                    # a resolver of your own; Spamhaus refuses public ones
#   PROBE_TO=you@gmail.com                # an outside mailbox you own; unset = no probe
#   PROBE_USER=noreply@mail.avedeus.ovh PROBE_PASSWORD=...   # a send-only mailbox
#   PROBE_CONNECT=127.0.0.1               # optional: connect here, check the certificate against MAIL_HOST
# Alerts: one when the set of problems changes, one when all clear (like
# health-check.sh). Pings HC_DELIVERY_URL if set.
# Cron (daily, 06:20):
#   20 6 * * * root /opt/MailServer/scripts/delivery-check.sh >>/var/log/mail-delivery.log 2>&1
set -uo pipefail
MAILCOW_DIR="${MAILCOW_DIR:-/opt/mailcow-dockerized}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
STATE_DIR="${STATE_DIR:-/var/lib/mailserver}"
# shellcheck disable=SC1091
[ -f /etc/mailserver/delivery.env ] && { set -a; . /etc/mailserver/delivery.env; set +a; }
# shellcheck source=scripts/lib/alerts.sh
. "$REPO_DIR/scripts/lib/alerts.sh"
HC_DELIVERY_URL="${HC_DELIVERY_URL:-}"
MAIL_HOST="${MAIL_HOST:-$(sed -n 's/^MAILCOW_HOSTNAME=//p' "$MAILCOW_DIR/mailcow.conf" 2>/dev/null)}"
export MAILCOW_DIR MAIL_HOST

echo "== $(date -u +%FT%TZ) delivery check"
out=$(python3 "$REPO_DIR/scripts/lib/mailcheck.py" 2>&1)
rc=$?
echo "$out" | sed 's/\t/  /g'

if [ "$rc" -ge 2 ]; then
  alert_send high "delivery check could not run" "$(echo "$out" | tail -n 3)"
  hc_ping "$HC_DELIVERY_URL" fail "$out"
  exit 2
fi
mkdir -p "$STATE_DIR"
state="$STATE_DIR/delivery.last"
current=$(echo "$out" | awk -F'\t' '$1 == "PROBLEM" {print $2}' | sort -u)
details=$(echo "$out" | awk -F'\t' '$1 == "PROBLEM" {print $3}')
previous=$(cat "$state" 2>/dev/null || true)
if [ "$current" != "$previous" ]; then
  if [ -n "$current" ]; then alert_send high "mail delivery: $(echo "$current" | wc -l) problem(s)" "$details"
  else alert_send default "mail delivery: all clear" "DNS, blocklists and the delivery probe pass again."; fi
  printf '%s' "$current" > "$state"
fi
if [ "$rc" = 0 ]; then hc_ping "$HC_DELIVERY_URL" success "ok"; else hc_ping "$HC_DELIVERY_URL" fail "$details"; fi
exit "$rc"
