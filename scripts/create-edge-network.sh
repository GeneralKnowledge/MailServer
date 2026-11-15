#!/usr/bin/env bash
# Create the small Docker network that joins mailcow's nginx to the invite
# site (and, later, the provisioning broker). Safe to re-run.
#
# Why: mailcow's Postfix trusts every address on mailcow's own network
# (mynetworks_style = subnet + permit_mynetworks), so a container there can
# send mail as anyone without a password. Our containers therefore stay off
# that network and meet nginx-mailcow on this one instead.
#
# The network is "internal": containers that are only on it (ours) get no
# route to the internet at all. nginx-mailcow keeps its normal networking
# through mailcow's own network.
#
# Must exist before mailcow starts with our docker-compose.override.yml.
set -euo pipefail
NAME="${EDGE_NETWORK:-avedeus-edge}"
SUBNET="${EDGE_SUBNET:-172.30.240.0/24}"
if docker network inspect "$NAME" >/dev/null 2>&1; then
  have="$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}} internal={{.Internal}}' "$NAME")"
  echo "$NAME exists: $have"
  [ "$have" = "$SUBNET internal=true" ] || { echo "ERROR: expected $SUBNET internal=true" >&2; exit 1; }
  exit 0
fi
docker network create --driver bridge --internal --subnet "$SUBNET" \
  --opt com.docker.network.bridge.name=br-avedeus "$NAME"
echo "created $NAME ($SUBNET, internal)"
