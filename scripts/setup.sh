#!/usr/bin/env bash
# M1 host preparation: updates, swap, base packages, Docker (official repo),
# and a checkout of mailcow at a release tag. Safe to re-run.
#
# It does NOT run mailcow's generate_config.sh or start mailcow: those are
# interactive and are done by hand following docs/deployment.md.
#
# Usage: sudo scripts/setup.sh mail.example.com [mailcow-tag]
#        Without a tag, the newest mailcow release tag is checked out (never
#        the moving master branch).
set -euo pipefail

HOSTNAME_FQDN="${1:?usage: setup.sh mail.example.com [mailcow-tag]}"
MAILCOW_TAG="${2:-}"
MAILCOW_DIR=/opt/mailcow-dockerized
[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)."; exit 1; }
. /etc/os-release
[ "$ID" = ubuntu ] || echo "WARNING: written for Ubuntu, found $ID $VERSION_ID"
echo "Architecture: $(uname -m)"

step() { printf '\n==> %s\n' "$*"; }

step "System updates and base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -yq full-upgrade
apt-get -yq install git openssl curl gawk coreutils grep jq dnsutils ca-certificates \
  unattended-upgrades chrony zstd rclone
dpkg-reconfigure -f noninteractive unattended-upgrades

step "SSH: keys only, no root login"
ADMIN_USER="${SUDO_USER:-ubuntu}"
KEYS="$(getent passwd "$ADMIN_USER" | cut -d: -f6)/.ssh/authorized_keys"
if [ -f /etc/ssh/sshd_config.d/01-hardening.conf ]; then
  echo "SKIPPED: SSH is managed by the host baseline (hardening/, docs/server-security-standard.md)"
elif [ -s "$KEYS" ]; then
  # sshd keeps the FIRST value it reads for each setting, and reads
  # sshd_config.d/*.conf in name order, so this file must sort first (01-)
  # or an image's own 50-cloud-init.conf could override it.
  rm -f /etc/ssh/sshd_config.d/60-mailserver.conf   # older location
  cat > /etc/ssh/sshd_config.d/01-mailserver.conf <<'SSHD'
# Written by MailServer scripts/setup.sh (kept first so it wins)
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
MaxAuthTries 3
X11Forwarding no
AllowAgentForwarding no
SSHD
  sshd -t && systemctl reload ssh
  # Check the settings sshd actually uses, not just what we wrote.
  effective="$(sshd -T -C user="$ADMIN_USER",host=localhost,addr=127.0.0.1 2>/dev/null)"
  for want in "passwordauthentication no" "kbdinteractiveauthentication no" "permitrootlogin no" "maxauthtries 3"; do
    echo "$effective" | grep -qx "$want" || { echo "ERROR: sshd is not using '$want'; check /etc/ssh/sshd_config.d/" >&2; exit 1; }
  done
  echo "sshd hardened and verified (key login for $ADMIN_USER confirmed present)"
else
  echo "SKIPPED: $KEYS is empty; not disabling password login to avoid a lock-out"
fi

step "Automatic security updates"
if [ -f /etc/apt/apt.conf.d/52hardening-unattended ]; then
  echo "SKIPPED: managed by the host baseline (hardening/)"
else
cat > /etc/apt/apt.conf.d/52mailserver-unattended <<'APT'
// Written by MailServer scripts/setup.sh
Unattended-Upgrade::Remove-Unused-Dependencies "true";
// Reboot for kernel updates at a quiet time (mail is queued by senders while down).
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
APT
fi

step "Hostname and time zone"
hostnamectl set-hostname "$HOSTNAME_FQDN"
timedatectl set-timezone Europe/London
systemctl enable --now chrony

step "Swap (2 GiB) if none"
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/90-swappiness.conf
fi
swapon --show

step "Docker Engine + Compose plugin from download.docker.com"
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  apt-get -yq remove docker.io docker-compose docker-compose-v2 podman-docker containerd runc 2>/dev/null || true
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get -yq install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
docker version --format 'Docker {{.Server.Version}} ({{.Server.Arch}})'
docker compose version

step "Host firewall check (Oracle images ship iptables rules)"
if iptables -S FORWARD | grep -q -- '-j REJECT'; then
  echo "NOTE: FORWARD chain has a REJECT rule. Docker inserts its own chains above it,"
  echo "      so published ports normally still work. Verify from outside after start-up."
fi
iptables -S INPUT | head -20

step "mailcow checkout"
umask 0022
fresh=0
if [ ! -d "$MAILCOW_DIR/.git" ]; then
  git clone https://github.com/mailcow/mailcow-dockerized "$MAILCOW_DIR"
  fresh=1
fi
cd "$MAILCOW_DIR"
git fetch --tags -q
if [ -z "$MAILCOW_TAG" ] && [ "$fresh" = 0 ]; then
  echo "Existing mailcow checkout left as it is (update with mailcow's own update.sh)"
elif [ -z "$MAILCOW_TAG" ]; then
  # mailcow tags releases by date (e.g. 2025-10a); take the newest one.
  MAILCOW_TAG="$(git tag --list '20*' --sort=-creatordate | head -1)"
  [ -n "$MAILCOW_TAG" ] || { echo "ERROR: no mailcow release tags found; pass one explicitly" >&2; exit 1; }
  echo "Using newest mailcow release: $MAILCOW_TAG"
fi
[ -z "$MAILCOW_TAG" ] || git -c advice.detachedHead=false checkout -q "$MAILCOW_TAG"
git log -1 --format='mailcow at %h (%cd)' --date=short

cat <<NEXT

Done. Next (see docs/deployment.md):
  cd $MAILCOW_DIR
  MAILCOW_HOSTNAME=$HOSTNAME_FQDN MAILCOW_TZ=Europe/London ./generate_config.sh
  sudo /opt/MailServer/scripts/harden-mailcow-conf.sh
  # review mailcow.conf, then:
  docker compose pull && docker compose up -d
NEXT
