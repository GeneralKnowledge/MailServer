#!/usr/bin/env bash
# M0 environment inspection for the mailcow deployment.
#
# READ-ONLY: this script installs nothing, changes no configuration and opens
# no ports. It only reads system state and makes outbound test connections.
#
# Usage:
#   bash m0-inspect.sh <domain> [mail-hostname]
#   e.g. bash m0-inspect.sh example.com mail.example.com
#
# Output goes to stdout and to ./m0-report-<host>-<timestamp>.txt.
# Some checks (firewall rules) use `sudo -n`; they are skipped, not prompted,
# if passwordless sudo is unavailable.

set -u
DOMAIN="${1:-}"
MAILHOST="${2:-${DOMAIN:+mail.$DOMAIN}}"
OUT="m0-report-$(hostname -s)-$(date -u +%Y%m%dT%H%M%SZ).txt"
exec > >(tee "$OUT") 2>&1

section() { printf '\n===== %s =====\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
run() { printf '$ %s\n' "$*"; "$@" 2>&1 || printf '(exit %s)\n' "$?"; }

# TCP connect test without needing nc: tcp_test host port timeout
tcp_test() {
  if timeout "${3:-8}" bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; then
    echo "OPEN     $1:$2"
  else
    echo "BLOCKED/TIMEOUT $1:$2"
  fi
}

# Read an SMTP banner on port 25 (proves real egress, not just a SYN)
smtp_banner() {
  local b
  b="$(timeout 12 bash -c "exec 3<>/dev/tcp/$1/25; head -1 <&3; printf 'QUIT\r\n' >&3" 2>/dev/null)"
  echo "${b:-(no banner from $1:25)}"
}

dns() { # dns <type> <name>
  if have dig; then dig +short "$1" "$2"
  elif have host; then host -t "$1" "$2"
  else getent ahosts "$2"; fi
}

section "Run info"
date -u; echo "Domain: ${DOMAIN:-<not given>}  Mail host: ${MAILHOST:-<not given>}"

section "OS and kernel"
run cat /etc/os-release
run uname -a
have systemd-detect-virt && run systemd-detect-virt

section "CPU / architecture (expect aarch64, 2 OCPU on Ampere)"
run uname -m
run nproc
have lscpu && lscpu | grep -Ei 'architecture|model name|vendor|^cpu\(s\)|thread|core'

section "Memory and swap (mailcow: >= 6 GiB RAM + 1 GiB swap)"
run free -h
run swapon --show

section "Disk (mailcow: >= 20 GiB plus mail storage)"
run df -hT /
[ -d /opt ] && run df -hT /opt
[ -d /var/lib/docker ] && run df -hT /var/lib/docker
run lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT

section "Time sync"
have timedatectl && run timedatectl

section "Hostname"
run hostname
run hostname -f
run cat /etc/hosts

section "Network interfaces, MTU, IPv6"
run ip -br addr
ip -o link | awk '{print $2, $4, $5}'
run ip -6 route show default

section "Public IP (as seen from the internet)"
PUBIP="$(curl -4 -s --max-time 8 https://ifconfig.me || curl -4 -s --max-time 8 https://api.ipify.org || true)"
echo "Public IPv4: ${PUBIP:-<unknown>}"
PUBIP6="$(curl -6 -s --max-time 8 https://api64.ipify.org || true)"
echo "Public IPv6: ${PUBIP6:-<none>}"

section "Reverse DNS (PTR) of public IP"
if [ -n "${PUBIP:-}" ]; then
  if have dig; then dig +short -x "$PUBIP"; else getent hosts "$PUBIP" || echo "(no PTR)"; fi
fi

section "Host DNS resolver"
run cat /etc/resolv.conf
have resolvectl && resolvectl status 2>/dev/null | head -30

section "Required tools for mailcow (git openssl curl awk sha1sum grep cut jq)"
for t in git openssl curl awk sha1sum grep cut jq dig; do
  printf '%-8s %s\n' "$t" "$(command -v $t || echo MISSING)"
done

section "Docker"
if have docker; then
  run docker version --format '{{.Server.Version}} ({{.Server.Arch}})'
  run docker compose version
  run docker ps -a
else
  echo "Docker NOT installed (mailcow needs Docker >= 24, Compose v2)"
fi
have docker-compose && run docker-compose version
dpkg -l 2>/dev/null | grep -Ei 'docker|containerd|podman' || true

section "Listening ports (mailcow needs 25 80 110 143 443 465 587 993 995 4190 free)"
if have ss; then run ss -tulpn; else run netstat -tulpn; fi
for p in 25 80 110 143 443 465 587 993 995 4190; do
  if ss -ltn "sport = :$p" 2>/dev/null | grep -q LISTEN; then echo "IN USE: $p"; fi
done

section "Mail/web services already installed"
for s in postfix exim4 sendmail dovecot apache2 nginx caddy bind9 named; do
  systemctl is-active --quiet "$s" 2>/dev/null && echo "ACTIVE: $s"
done
echo "(nothing listed above = none active)"

section "Host firewall (Oracle Ubuntu images ship restrictive iptables rules)"
have ufw && { sudo -n ufw status verbose 2>/dev/null || echo "ufw: need sudo"; }
have firewall-cmd && { sudo -n firewall-cmd --state 2>/dev/null || true; }
if sudo -n true 2>/dev/null; then
  run sudo -n iptables -S
  run sudo -n iptables -S DOCKER-USER
  run sudo -n ip6tables -S
  have nft && sudo -n nft list ruleset 2>/dev/null | head -80
else
  echo "Skipped iptables listing (no passwordless sudo). Re-run with sudo for this section."
fi
[ -f /etc/iptables/rules.v4 ] && { echo "--- /etc/iptables/rules.v4 present"; sudo -n cat /etc/iptables/rules.v4 2>/dev/null || echo "(need sudo to read)"; }

section "Outbound connectivity"
tcp_test 1.1.1.1 53 5
tcp_test github.com 443 8
tcp_test registry-1.docker.io 443 8
tcp_test acme-v02.api.letsencrypt.org 443 8
echo "--- Outbound SMTP port 25 (Oracle blocks this by default for tenancies created after 2021-06-23)"
tcp_test gmail-smtp-in.l.google.com 25 10
tcp_test outlook-com.olc.protection.outlook.com 25 10
echo "Banner check (a banner means port 25 egress really works):"
smtp_banner gmail-smtp-in.l.google.com
echo "--- Relay ports (fallback if 25 is blocked)"
tcp_test smtp.gmail.com 587 8
tcp_test smtp.gmail.com 465 8

if [ -n "$DOMAIN" ]; then
  section "DNS for $DOMAIN (read-only lookups)"
  for t in NS SOA A AAAA MX TXT CAA; do echo "--- $t $DOMAIN"; dns "$t" "$DOMAIN"; done
  echo "--- TXT _dmarc.$DOMAIN"; dns TXT "_dmarc.$DOMAIN"
  echo "--- TXT dkim._domainkey.$DOMAIN"; dns TXT "dkim._domainkey.$DOMAIN"
  for n in "$MAILHOST" "autodiscover.$DOMAIN" "autoconfig.$DOMAIN" "www.$DOMAIN"; do
    echo "--- A/AAAA/CNAME $n"; dns A "$n"; dns AAAA "$n"; dns CNAME "$n"
  done
  echo "--- SRV records"
  for s in _autodiscover._tcp _imaps._tcp _submission._tcp _submissions._tcp; do
    echo "$s: $(dns SRV "$s.$DOMAIN" | tr '\n' ' ')"
  done
  echo "--- Does $MAILHOST point at this server ($PUBIP)?"
  dns A "$MAILHOST" | grep -qx "${PUBIP:-x}" && echo "YES" || echo "NO / not yet"
fi

section "Done"
echo "Report written to $OUT"
echo "Also check from OUTSIDE the server: OCI VCN security list / NSG ingress for"
echo "TCP 25,80,443,465,587,993,995,4190 (and 110,143 only if needed)."
