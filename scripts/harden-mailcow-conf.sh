#!/usr/bin/env bash
# Tighten mailcow.conf after generate_config.sh. Safe to re-run; makes a
# timestamped backup first. Apply with: cd /opt/mailcow-dockerized && docker compose up -d
#
# - Plain IMAP (143), POP3 (110) and POP3S (995) only on localhost: users get
#   IMAPS 993 and SMTP 465/587, which every current mail app supports.
# - http -> https redirect.
set -euo pipefail
CONF="${1:-/opt/mailcow-dockerized/mailcow.conf}"
[ -f "$CONF" ] || { echo "no $CONF"; exit 1; }
cp -p "$CONF" "$CONF.bak.$(date +%Y%m%d%H%M%S)"

set_kv() {  # set_kv KEY VALUE  (replace or append)
  if grep -q "^$1=" "$CONF"; then sed -i "s|^$1=.*|$1=$2|" "$CONF"; else echo "$1=$2" >> "$CONF"; fi
  echo "  $1=$2"
}
echo "Updating $CONF:"
set_kv IMAP_PORT 127.0.0.1:143
set_kv POP_PORT 127.0.0.1:110
set_kv POPS_PORT 127.0.0.1:995
set_kv HTTP_REDIRECT y
chmod 600 "$CONF"
echo "Done. Apply: cd $(dirname "$CONF") && docker compose up -d"
