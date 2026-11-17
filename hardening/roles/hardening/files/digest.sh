#!/usr/bin/env bash
# Daily security digest: the last 24 hours of SSH logins and probes, firewall
# blocks (scans), fail2ban bans, and the exposure audit. Sent through the same
# ntfy/healthchecks settings as the mail server's alerts; printed when run by
# hand. High priority when the audit finds a FAIL, so a new public port or a
# reverted SSH setting is noticed the next morning at the latest.
set -uo pipefail
export ALERTS_ENV="${ALERTS_ENV:-/etc/hardening/alerts.env}"
# shellcheck source=/dev/null
. /usr/local/lib/hardening/alerts.sh
HC_DIGEST_URL="${HC_DIGEST_URL:-}"
since="-24h"

top() { sort | uniq -c | sort -rn | head -"$1" | awk '{c=$1; $1=""; printf "  %s (%s)\n", substr($0, 2), c}'; }

ssh_log="$(journalctl -q -o cat --since "$since" -u ssh.service -u sshd.service 2>/dev/null)"
accepted="$(grep -E '^Accepted (publickey|password|keyboard-interactive)' <<<"$ssh_log")"
probes="$(grep -E 'Invalid user|Failed (password|publickey)|authenticating user|invalid user|Did not receive identification|banner exchange' <<<"$ssh_log")"
probe_ips="$(grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' <<<"$probes")"
kern="$(journalctl -q -k -o cat --since "$since" 2>/dev/null)"
blocks="$(grep -F '[UFW BLOCK]' <<<"$kern")"
dblocks="$(grep -F '[HARDENING DOCKER BLOCK]' <<<"$kern")"

report="$(
  echo "Last 24 hours on $ALERT_HOST"
  echo
  echo "SSH logins: $(grep -c . <<<"$accepted")"
  [ -n "$accepted" ] && sed -E 's/^Accepted ([a-z-]+) for ([^ ]+) from ([^ ]+).*/\2 from \3 (\1)/' <<<"$accepted" | top 10
  echo "SSH probes and failures: $(grep -c . <<<"$probes") log lines from $(sort -u <<<"$probe_ips" | grep -c .) addresses"
  [ -n "$probe_ips" ] && top 5 <<<"$probe_ips"
  echo "Firewall blocks: $(grep -c . <<<"$blocks") logged from $(grep -oE 'SRC=[^ ]+' <<<"$blocks" | sort -u | grep -c .) addresses (ufw logs at most a few per second)"
  [ -n "$blocks" ] && { echo " most probed ports:"; grep -oE 'DPT=[0-9]+' <<<"$blocks" | cut -d= -f2 | top 5; }
  [ -n "$dblocks" ] && echo "Docker guard blocks: $(grep -c . <<<"$dblocks") logged (published ports not declared public)"
  if command -v fail2ban-client >/dev/null; then
    for jail in sshd recidive; do
      s="$(fail2ban-client status "$jail" 2>/dev/null)" || continue
      echo "fail2ban $jail: $(awk -F'\t' '/Currently banned/ {print $2}' <<<"$s" | xargs) banned now, $(awk -F'\t' '/Total banned/ {print $2}' <<<"$s" | xargs) since start"
    done
  fi
  echo
  echo "Audit (warnings and failures only):"
  /usr/local/sbin/hardening-audit --quiet 2>&1 || true
)"
audit_fail=0
grep -q '^FAIL' <<<"$report" && audit_fail=1

if [ -t 1 ]; then echo "$report"; fi
if [ "$audit_fail" = 1 ]; then
  alert_send high "security digest: audit FAILED" "$report"
else
  alert_send low "security digest" "$report"
fi
hc_ping "$HC_DIGEST_URL" success "$report"
logger -t hardening-digest "digest sent (audit_fail=$audit_fail)"
