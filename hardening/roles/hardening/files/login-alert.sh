#!/usr/bin/env bash
# Called by pam_exec when an SSH session opens (/etc/pam.d/sshd). Sends one
# push per user and source address per 10 minutes, so an Ansible run is one
# alert, not fifty. Returns at once and never fails the login.
[ "${PAM_TYPE:-}" = open_session ] || exit 0
user="${PAM_USER:-?}" from="${PAM_RHOST:-?}"
msg="SSH login: $user from $from at $(date '+%F %T %Z')"
logger -t hardening-login -- "$msg"

state=/run/hardening-login
mkdir -m 700 "$state" 2>/dev/null
stamp="$state/$(printf '%s' "$user@$from" | md5sum | cut -c1-16)"
if [ -n "$(find "$stamp" -mmin -10 2>/dev/null)" ]; then exit 0; fi
touch "$stamp"

(
  export ALERTS_ENV=/etc/hardening/alerts.env
  # shellcheck source=/dev/null
  . /usr/local/lib/hardening/alerts.sh
  alert_send default "SSH login: $user" "$msg"
) </dev/null >/dev/null 2>&1 &
exit 0
