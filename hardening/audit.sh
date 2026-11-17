#!/usr/bin/env bash
# Check one Ubuntu host against the Avedeus host baseline
# (docs/server-security-standard.md). Read-only: it changes nothing.
#
# Works on any host, with or without the Ansible baseline applied, so it is
# also the audit for machines that already exist:
#
#   scp hardening/audit.sh host: && ssh host sudo bash audit.sh
#   sudo PUBLIC_TCP="22 80 443" bash audit.sh     # declare what may be public
#   sudo hardening-audit --quiet                  # installed copy, WARN/FAIL only
#
# What the host is meant to expose comes from /etc/hardening/policy.env
# (written by the playbook) or, without it, from PUBLIC_TCP / PUBLIC_UDP in
# the environment (default: only SSH). Exit status 1 if anything FAILs.
set -u   # no pipefail: "cmd | grep -q" would fail on SIGPIPE

QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1
[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo): the firewall and sshd can only be read by root." >&2; exit 2; }

POLICY_FILE="${POLICY_FILE:-/etc/hardening/policy.env}"
policy_source="defaults (no $POLICY_FILE)"
if [ -f "$POLICY_FILE" ]; then
  # shellcheck disable=SC1090
  . "$POLICY_FILE"
  policy_source="$POLICY_FILE"
fi
PUBLIC_TCP="${PUBLIC_TCP:-22}"
PUBLIC_UDP="${PUBLIC_UDP:-}"
SSH_TAILSCALE_ONLY="${SSH_TAILSCALE_ONLY:-0}"
DOCKER_GUARD="${DOCKER_GUARD:-0}"
FAIL2BAN="${FAIL2BAN:-0}"
LOGIN_ALERTS="${LOGIN_ALERTS:-0}"
DIGEST="${DIGEST:-0}"

n_pass=0 n_warn=0 n_fail=0
pass() { n_pass=$((n_pass + 1)); [ "$QUIET" = 1 ] || printf 'PASS  %s\n' "$*"; }
warn() { n_warn=$((n_warn + 1)); printf 'WARN  %s\n' "$*"; }
fail() { n_fail=$((n_fail + 1)); printf 'FAIL  %s\n' "$*"; }
info() { [ "$QUIET" = 1 ] || printf 'INFO  %s\n' "$*"; }
in_list() { case " $2 " in *" $1 "*) return 0 ;; esac; return 1; }
have() { command -v "$1" >/dev/null 2>&1; }

[ "$QUIET" = 1 ] || echo "Host baseline audit: $(hostname -f 2>/dev/null || hostname), $(date -u +%FT%TZ), policy: $policy_source"
[ "$QUIET" = 1 ] || echo "May be public: tcp [$PUBLIC_TCP] udp [${PUBLIC_UDP:-none}]$([ "$SSH_TAILSCALE_ONLY" = 1 ] && echo ', SSH over the tailnet only')"

# --- S1 accounts -------------------------------------------------------------
uid0="$(awk -F: '$3 == 0 && $1 != "root" {print $1}' /etc/passwd | xargs)"
[ -z "$uid0" ] && pass "accounts: only root has uid 0" || fail "accounts: other uid-0 accounts: $uid0"
empty="$(awk -F: '$2 == "" {print $1}' /etc/shadow | xargs)"
[ -z "$empty" ] && pass "accounts: no account has an empty password" || fail "accounts: empty password: $empty"
docker_members="$(getent group docker | cut -d: -f4)"
[ -n "$docker_members" ] && info "accounts: docker group (root-equivalent): $docker_members"

# --- S2 SSH ------------------------------------------------------------------
if have sshd && ! eff="$(sshd -T -C user=nobody,host=localhost,addr=127.0.0.1 2>&1)"; then
  fail "ssh: could not read sshd's settings: $(head -1 <<<"$eff")"
elif have sshd; then
  check_sshd() {
    if grep -qx "$1" <<<"$eff"; then pass "ssh: $1"; else fail "ssh: expected '$1', sshd uses '$(grep "^${1%% *} " <<<"$eff")'"; fi
  }
  check_sshd "passwordauthentication no"
  check_sshd "kbdinteractiveauthentication no"
  check_sshd "permitrootlogin no"
  check_sshd "pubkeyauthentication yes"
  check_sshd "x11forwarding no"
  tries="$(awk '$1 == "maxauthtries" {print $2}' <<<"$eff")"
  [ "${tries:-6}" -le 3 ] && pass "ssh: maxauthtries $tries" || warn "ssh: maxauthtries ${tries:-?} (baseline: 3)"
else
  info "ssh: no OpenSSH server installed"
fi

# --- S3 firewall -------------------------------------------------------------
ufw_status=""
if have ufw; then ufw_status="$(ufw status verbose 2>/dev/null)"; fi
if grep -q '^Status: active' <<<"$ufw_status"; then
  pass "firewall: ufw active"
  grep -q 'deny (incoming)' <<<"$ufw_status" && pass "firewall: incoming denied by default" \
    || fail "firewall: default incoming policy is not deny"
  # Rules open to everyone (no source address, no single interface).
  while read -r line; do
    case "$line" in "ufw allow "*|"ufw limit "*) ;; *) continue ;; esac
    case "$line" in *" from "*|*" on "*) continue ;; esac
    if [[ $line =~ port\ ([0-9:,]+)(\ proto\ ([a-z]+))? ]]; then
      port="${BASH_REMATCH[1]}"; proto="${BASH_REMATCH[3]:-any}"
    elif [[ $line =~ ^ufw\ (allow|limit)\ ([0-9:,]+)(/([a-z]+))? ]]; then
      port="${BASH_REMATCH[2]}"; proto="${BASH_REMATCH[4]:-any}"
    else
      warn "firewall: rule not checked (application profile?): $line"; continue
    fi
    list="$PUBLIC_TCP"; [ "$proto" = udp ] && list="$PUBLIC_UDP"
    if [ "$port" = 22 ] && [ "$SSH_TAILSCALE_ONLY" = 1 ]; then
      fail "firewall: SSH is open to the internet, but this host should take SSH over the tailnet only"
    elif ! in_list "$port" "$list"; then
      warn "firewall: ufw opens $port/$proto to everyone, not in the declared list"
    fi
  done < <(ufw show added 2>/dev/null)
else
  fail "firewall: ufw is not active (every listening port below is reachable if the provider firewall allows it)"
fi

oracle=0
for f in /etc/iptables/rules.v4.pre-hardening /etc/iptables/rules.v4; do
  grep -qs InstanceServices "$f" && oracle=1
done
if [ "$oracle" = 1 ]; then
  oracle_live=0
  iptables -S INPUT 2>/dev/null | grep -q icmp-host-prohibited && oracle_live=1
  if ! grep -q '^Status: active' <<<"$ufw_status"; then
    [ "$oracle_live" = 1 ] && info "firewall: Oracle image; its stock rules are the host firewall (22 open to all, the rest rejected; Docker ports bypass them)"
  elif [ "$oracle_live" = 1 ]; then
    warn "firewall: Oracle's stock REJECT rules are still loaded next to ufw; reboot once to finish the hand-over"
  else
    pass "firewall: Oracle image, INPUT/FORWARD handed over to ufw"
  fi
  chain=OUTPUT; grep -q '^Status: active' <<<"$ufw_status" && chain=ufw-after-output   # what loads at boot
  if iptables -S "$chain" 2>/dev/null | grep -q -- '-d 169.254.0.0/16 -j InstanceServices'; then
    pass "firewall: Oracle's 169.254.0.0/16 rules (boot volume, metadata) loaded"
  else
    fail "firewall: Oracle's InstanceServices rules are not loaded (only root should reach the iSCSI boot volume)"
  fi
fi

# --- S3 Docker-published ports (bypass ufw) ------------------------------------
guard=0
if have iptables && iptables -S DOCKER-USER 2>/dev/null | grep -q -- '-j hardening-docker-in'; then guard=1; fi
if have docker && docker info >/dev/null 2>&1; then
  [ "$guard" = 1 ] && pass "docker: guard active (published ports filtered in DOCKER-USER)" \
    || { [ "$DOCKER_GUARD" = 1 ] && fail "docker: guard expected but not loaded (run: ufw reload)"; } \
    || info "docker: no guard on this host; published ports must all be meant to be public"
  while read -r name ports; do
    for mapping in ${ports//,/ }; do
      # 0.0.0.0:8080->80/tcp, [::]:8080->80/tcp, 127.0.0.1:5432->5432/tcp
      case "$mapping" in *"->"*) ;; *) continue ;; esac
      host="${mapping%%->*}"; hostip="${host%:*}"; hport="${host##*:}"; proto="${mapping##*/}"
      case "$hostip" in 127.*|"[::1]"|100.*) continue ;; esac
      list="$PUBLIC_TCP"; [ "$proto" = udp ] && list="$PUBLIC_UDP"
      if in_list "$hport" "$list"; then
        pass "docker: $name publishes $hport/$proto (declared public)"
      elif [ "$guard" = 1 ]; then
        info "docker: $name publishes $hport/$proto on $hostip; the guard blocks it from the internet"
      else
        fail "docker: $name publishes $hport/$proto on $hostip; reachable from the internet, ufw does not apply"
      fi
    done
  done < <(docker ps --format '{{.Names}} {{.Ports}}' | sed 's/, /,/g' | awk 'NF > 1')
  ss -Hltn 2>/dev/null | awk '{print $4}' | grep -qE '[:.](2375|2376)$' \
    && fail "docker: the Docker API listens on TCP 2375/2376 (root access for whoever reaches it)"
fi

# --- listening sockets ---------------------------------------------------------
docker_ports="$( { have docker && docker ps --format '{{.Ports}}' 2>/dev/null; } | grep -oE ':[0-9]+->' | tr -d ':->' | sort -u | xargs)"
listeners() {  # proto ss-flags
  ss -H "$2" 2>/dev/null | awk '{print $4}' | while read -r addr; do
    port="${addr##*:}"; ip="${addr%:*}"
    case "$ip" in 127.*|"[::1]"|*%lo|100.*|"[fd7a:115c:a1e0:"*) continue ;; esac
    echo "$port $ip"
  done | sort -u -n
}
while read -r port ip; do
  [ -n "$port" ] || continue
  in_list "$port" "$PUBLIC_TCP" && continue
  in_list "$port" "$docker_ports" && continue
  if grep -q '^Status: active' <<<"$ufw_status"; then
    info "listening: tcp $port on $ip, not declared public (ufw blocks it)"
  else
    warn "listening: tcp $port on $ip, not declared public and no host firewall"
  fi
done < <(listeners tcp -ltn)
while read -r port ip; do
  [ -n "$port" ] || continue
  case "$port" in 68|546|5353) continue ;; esac   # DHCP, DHCPv6 client, mDNS
  in_list "$port" "$PUBLIC_UDP" && continue
  in_list "$port" "$docker_ports" && continue
  grep -q '^Status: active' <<<"$ufw_status" || warn "listening: udp $port on $ip, not declared public and no host firewall"
done < <(listeners udp -lun)

# --- S4 brute-force bans ---------------------------------------------------------
if have fail2ban-client && fail2ban-client status sshd >/dev/null 2>&1; then
  pass "bans: fail2ban sshd jail running"
elif have cscli && systemctl is-active -q crowdsec 2>/dev/null; then
  pass "bans: CrowdSec running"
elif [ "$FAIL2BAN" = 1 ]; then
  fail "bans: fail2ban expected but its sshd jail is not running"
else
  warn "bans: neither fail2ban nor CrowdSec is running"
fi

# --- S5 updates and time -----------------------------------------------------------
if apt-config dump 2>/dev/null | grep -q 'APT::Periodic::Unattended-Upgrade "1"' \
   && systemctl is-enabled -q apt-daily-upgrade.timer 2>/dev/null; then
  pass "updates: unattended security upgrades on"
else
  fail "updates: unattended security upgrades are off"
fi
if [ -f /var/run/reboot-required ]; then
  days=$(( ( $(date +%s) - $(stat -c %Y /var/run/reboot-required) ) / 86400 ))
  [ "$days" -ge 7 ] && fail "updates: reboot pending for $days days ($(xargs < /var/run/reboot-required.pkgs 2>/dev/null))" \
    || warn "updates: reboot pending ($days days)"
else
  pass "updates: no reboot pending"
fi
sec="$(apt-get -s -o Debug::NoLocking=1 dist-upgrade 2>/dev/null | grep -c '^Inst .*-security')"
[ "${sec:-0}" -eq 0 ] && pass "updates: no security updates waiting" || warn "updates: $sec security updates waiting"
[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ] && pass "time: clock synchronised" \
  || warn "time: clock not synchronised (log times and TOTP depend on it)"

# --- S6 private access -----------------------------------------------------------------
if [ "$SSH_TAILSCALE_ONLY" = 1 ]; then
  if have tailscale && tailscale status >/dev/null 2>&1; then pass "tailscale: connected"
  else fail "tailscale: SSH is tailnet-only but Tailscale is not connected (console access needed)"; fi
fi

# --- S7 alerts ---------------------------------------------------------------------------
if grep -qs 'hardening-login-alert' /etc/pam.d/sshd; then pass "alerts: SSH logins alert"
elif [ "$LOGIN_ALERTS" = 1 ]; then fail "alerts: login alerts expected but not in /etc/pam.d/sshd"
else warn "alerts: no alert on SSH logins"; fi
if [ -f /etc/hardening/alerts.env ]; then
  # shellcheck disable=SC1091
  if ( . /etc/hardening/alerts.env; [ -n "${NTFY_URL:-}" ] ); then pass "alerts: push destination set"
  else warn "alerts: no NTFY_URL, alerts only reach the journal"; fi
fi
if [ "$DIGEST" = 1 ]; then
  systemctl is-enabled -q hardening-digest.timer 2>/dev/null && pass "alerts: daily digest on" \
    || fail "alerts: daily digest expected but its timer is off"
fi

# --- kernel ----------------------------------------------------------------------------------
[ "$(sysctl -n net.ipv4.conf.all.accept_redirects 2>/dev/null)" = 0 ] && pass "kernel: ICMP redirects ignored" \
  || warn "kernel: ICMP redirects accepted"

[ "$QUIET" = 1 ] || echo "Summary: $n_pass pass, $n_warn warn, $n_fail fail"
[ "$n_fail" -eq 0 ]
