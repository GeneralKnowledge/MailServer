#!/usr/bin/env bash
# CI proof for the host baseline, run on a throwaway GitHub Ubuntu runner (a
# real VM with systemd, sshd and Docker). It applies the playbook to the
# runner itself, then checks from an "outside" network namespace, which plays
# the internet, that only declared ports answer: including the Docker port
# bypass, with the guard off (reachable, audit fails) and on (blocked).
#
#   sudo is needed; never run this on a machine you care about.
set -euo pipefail
cd "$(dirname "$0")/.."
step() { printf '\n==> %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

HOST_IP=10.99.0.1
OUT_IP=10.99.0.2
ME="$(id -un)"

step "Prepare the runner"
# The baseline refuses to turn off passwords for an account with no key.
mkdir -p ~/.ssh && chmod 700 ~/.ssh
[ -s ~/.ssh/authorized_keys ] || { ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/ci_key; cat ~/.ssh/ci_key.pub >> ~/.ssh/authorized_keys; }
# Keep apt's own timers and needrestart from interfering with the job.
sudo systemctl stop apt-daily.timer apt-daily-upgrade.timer unattended-upgrades 2>/dev/null || true
sudo mkdir -p /etc/needrestart/conf.d
echo "\$nrconf{restart} = 'l';" | sudo tee /etc/needrestart/conf.d/zz-ci.conf >/dev/null
# "Outside": a network namespace joined to the host by a veth pair.
sudo ip netns add outside
sudo ip link add veth-host type veth peer name veth-out
sudo ip link set veth-out netns outside
sudo ip addr add "$HOST_IP/24" dev veth-host && sudo ip link set veth-host up
sudo ip netns exec outside ip addr add "$OUT_IP/24" dev veth-out
sudo ip netns exec outside ip link set veth-out up
sudo ip netns exec outside ip link set lo up

cat > /tmp/ci-inventory.yml <<EOF
all:
  hosts:
    ci-runner:
      ansible_connection: local
      ansible_python_interpreter: /usr/bin/python3
      hardening_admin_user: $ME
      hardening_external_interfaces: [eth0, veth-host]
      hardening_public_tcp_ports: [8090]
      hardening_docker_public_tcp_ports: [8443]
EOF
play() { ansible-playbook -i /tmp/ci-inventory.yml site.yml "$@"; }

step "Apply the baseline"
play
step "Apply again: nothing may change"
play | tee /tmp/second-run.log
grep -qE 'ci-runner +: ok=[0-9]+ +changed=0 ' /tmp/second-run.log || fail "second run changed something"

step "Start services: containers on 8443 (declared) and 8080 (not), host servers on 8090 (declared) and 9000 (not)"
start_services() {
  docker rm -f web8080 web8443 >/dev/null 2>&1 || true
  docker run -d --name web8443 -p 8443:80 busybox httpd -f -p 80 -h /etc >/dev/null
  docker run -d --name web8080 -p 8080:80 busybox httpd -f -p 80 -h /etc >/dev/null
  sleep 2
}
start_services
(cd /etc && nohup python3 -m http.server 8090 >/dev/null 2>&1 &)
(cd /etc && nohup python3 -m http.server 9000 >/dev/null 2>&1 &)
sleep 1

from_outside() {  # port -> prints "open" or "closed"
  if sudo ip netns exec outside curl -s -m 4 -o /dev/null "http://$HOST_IP:$1/hostname"; then echo open; else echo closed; fi
}
expect() {  # label port open|closed
  got="$(from_outside "$2")"
  printf '  %-34s port %-5s %s\n' "$1" "$2" "$got"
  [ "$got" = "$3" ] || fail "$1: port $2 is $got from outside, expected $3"
}
check_all() {
  expect "container, declared public" 8443 open
  expect "container, not declared" 8080 closed
  expect "host service, declared public" 8090 open
  expect "host service, not declared" 9000 closed
  sudo ip netns exec outside bash -c "exec 3<>/dev/tcp/$HOST_IP/22" 2>/dev/null || fail "ssh not reachable from outside"
  echo "  ssh                                port 22    open (rate-limited)"
}

step "Check from outside"
check_all
step "Same after ufw reload (guard must survive, not duplicate)"
before="$(sudo iptables -S hardening-docker-in | wc -l)"
sudo ufw reload >/dev/null
[ "$(sudo iptables -S hardening-docker-in | wc -l)" = "$before" ] || fail "guard rules changed on reload"
check_all
step "Same after a Docker restart"
sudo systemctl restart docker
start_services
check_all

step "Audit passes"
sudo hardening-audit

step "Guard off: the bypass is real and the audit catches it"
play -e '{"hardening_docker_guard": false}' >/dev/null
expect "container, not declared (no guard)" 8080 open
expect "host service, not declared" 9000 closed
if sudo hardening-audit --quiet; then fail "audit passed with a public undeclared Docker port"; fi
echo "  audit failed as it should"

step "Guard back on"
play >/dev/null
check_all

step "Daily digest counts the blocked probes"
digest="$(sudo hardening-digest 2>&1 || true)"
echo "$digest"
grep -qE 'Firewall blocks: [1-9]' <<<"$digest" || fail "digest shows no firewall blocks"
grep -qE 'Docker guard blocks: [1-9]' <<<"$digest" || fail "digest shows no Docker guard blocks"
echo; echo "All host baseline checks passed."
