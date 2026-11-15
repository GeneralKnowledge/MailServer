#!/usr/bin/env bash
# Identity stack smoke test (PR 11): starts Keycloak + Postgres from
# infrastructure/identity, waits for them to be healthy and checks that the
# realm-as-code imported and behaves as the ADRs require. Runs in CI on every
# push; on the server it is part of the ADR 0006 validation gate (gate
# conditions 2 and 3: it prints memory use and round-trips a database dump).
#
# Usage: scripts/identity-smoke.sh            (needs infrastructure/identity/identity.env
#                                              and the avedeus-edge network)
#        KEEP=1 scripts/identity-smoke.sh     leave the stack running afterwards
# Exit code 0 = every check passed.
set -euo pipefail
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
DIR="$REPO_DIR/infrastructure/identity"
# --env-file: the compose file itself uses MAIL_HOSTNAME (extra_hosts, PR 17).
ID="docker compose -f $DIR/docker-compose.yml --env-file $DIR/identity.env"
KC="http://172.30.240.12:8080/auth"     # Keycloak's fixed address on avedeus-edge
KEEP="${KEEP:-0}"
fails=0
pass() { echo "ok: $*"; }
fail() { echo "FAIL: $*"; fails=$((fails + 1)); }

[ -f "$DIR/identity.env" ] || { echo "missing $DIR/identity.env (copy identity.env.example)" >&2; exit 2; }
# shellcheck disable=SC1091
. "$DIR/identity.env"
cleanup() { [ "$KEEP" = 1 ] || $ID down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

$ID up -d
echo "waiting for Keycloak to be healthy (first start builds and imports; up to 5 minutes)"
state=""
for _ in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$($ID ps -q keycloak)" 2>/dev/null || true)
  [ "$state" = healthy ] && break
  sleep 5
done
if [ "$state" != healthy ]; then
  $ID logs --tail 80 keycloak
  echo "FAIL: Keycloak not healthy (state: ${state:-none})"; exit 1
fi
pass "Keycloak healthy"

# 1. The realm imported, under /auth, with the public hostname as issuer.
issuer=$(curl -fsS "$KC/realms/avedeus/.well-known/openid-configuration" | jq -r .issuer)
[ "$issuer" = "$KC_HOSTNAME/realms/avedeus" ] && pass "realm avedeus imported, issuer $issuer" \
  || fail "issuer is '$issuer', expected $KC_HOSTNAME/realms/avedeus"

# 2. The control plane's client gets a token (client credentials) and may read
#    the realm's clients; placeholders were substituted from identity.env.
token=$(curl -fsS -d grant_type=client_credentials -d client_id=avedeus-control-plane \
  --data-urlencode "client_secret=$CONTROL_PLANE_OIDC_SECRET" \
  "$KC/realms/avedeus/protocol/openid-connect/token" | jq -r .access_token)
if [ -n "$token" ] && [ "$token" != null ]; then pass "control-plane client authenticates"; else fail "control-plane client got no token"; fi
groups=$(curl -fsS -H "Authorization: Bearer $token" "$KC/admin/realms/avedeus/groups" | jq -r '[.[].name] | sort | join(",")')
[ "$groups" = "admins,users" ] && pass "groups as code: $groups" || fail "groups are '$groups'"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $token" "$KC/admin/realms/avedeus/clients")
[ "$code" = 403 ] && pass "control-plane client cannot read client configuration (403)" \
  || fail "control-plane client reading clients returned $code, expected 403"

# 2b. The control plane can create an invited user the way PR 12 does
#     (group, password, the admin-only mailcow_template attribute), then remove it.
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
  -d '{"username":"smoke-user","email":"smoke-user@'"$MAIL_HOSTNAME"'","emailVerified":true,"enabled":true,"groups":["/users"],"attributes":{"mailcow_template":["smoke"]},"credentials":[{"type":"password","value":"Smoke-Test-Password-1","temporary":false}]}' \
  "$KC/admin/realms/avedeus/users")
uid=$(curl -fsS -H "Authorization: Bearer $token" "$KC/admin/realms/avedeus/users?username=smoke-user&exact=true" | jq -r '.[0].id // empty')
tmpl=$(curl -fsS -H "Authorization: Bearer $token" "$KC/admin/realms/avedeus/users/$uid" | jq -r '.attributes.mailcow_template[0] // empty')
grp=$(curl -fsS -H "Authorization: Bearer $token" "$KC/admin/realms/avedeus/users/$uid/groups" | jq -r '[.[].name]|join(",")')
if [ "$code" = 201 ] && [ "$tmpl" = smoke ] && [ "$grp" = users ]; then
  pass "control plane creates a user with group and mailcow_template"
else
  fail "user creation: HTTP $code, template '$tmpl', groups '$grp'"
fi
[ -n "$uid" ] && curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $token" "$KC/admin/realms/avedeus/users/$uid"

# 3. mailcow's client exists with the substituted redirect, and reading users
#    is all its service account may do.
mtoken=$(curl -fsS -d grant_type=client_credentials -d client_id=mailcow \
  --data-urlencode "client_secret=$MAILCOW_OIDC_SECRET" \
  "$KC/realms/avedeus/protocol/openid-connect/token" | jq -r .access_token)
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $mtoken" "$KC/admin/realms/avedeus/users?max=1")
[ "$code" = 200 ] && pass "mailcow client may read users (for its sync)" || fail "mailcow client reading users returned $code"
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $mtoken" -H 'Content-Type: application/json' \
  -d '{"username":"smoke-should-not-exist","enabled":true}' "$KC/admin/realms/avedeus/users")
[ "$code" = 403 ] && pass "mailcow client cannot create users (403)" || fail "mailcow client creating a user returned $code"

# 3b. The portal client (PR 14) shows the login page for its exact redirect
#     and refuses any other.
auth="$KC/realms/avedeus/protocol/openid-connect/auth?client_id=avedeus-portal&response_type=code&scope=openid&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256"
good=$(curl -s -o /dev/null -w '%{http_code}' "$auth&redirect_uri=https://$MAIL_HOSTNAME/account/callback")
bad=$(curl -s -o /dev/null -w '%{http_code}' "$auth&redirect_uri=https://evil.example/account/callback")
if [ "$good" = 200 ] && [ "$bad" = 400 ]; then pass "portal client: exact redirect only"
else fail "portal client: good redirect $good, bad redirect $bad"; fi

# 3c. The admin console client (PR 15): same exact-redirect rule, and the
#     realm still has its default browser flow next to the console's own.
auth="$KC/realms/avedeus/protocol/openid-connect/auth?client_id=avedeus-admin&response_type=code&scope=openid&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256"
good=$(curl -s -o /dev/null -w '%{http_code}' "$auth&redirect_uri=https://$MAIL_HOSTNAME/console/callback")
bad=$(curl -s -o /dev/null -w '%{http_code}' "$auth&redirect_uri=https://$MAIL_HOSTNAME/account/callback")
if [ "$good" = 200 ] && [ "$bad" = 400 ]; then pass "admin console client: exact redirect only"
else fail "admin console client: good redirect $good, bad redirect $bad"; fi

# 4. No password grant (ADR 0015): Keycloak never checks a mail-client password.
err=$(curl -s -d grant_type=password -d client_id=mailcow --data-urlencode "client_secret=$MAILCOW_OIDC_SECRET" \
  -d username=nobody -d password=nothing "$KC/realms/avedeus/protocol/openid-connect/token" | jq -r .error)
[ "$err" = unauthorized_client ] && pass "password grant refused ($err)" || fail "password grant answered '$err'"

# 5. Self-registration is off: accounts come from invitations only.
code=$(curl -s -o /dev/null -w '%{http_code}' "$KC/realms/avedeus/protocol/openid-connect/registrations?client_id=mailcow&response_type=code&redirect_uri=https://$MAIL_HOSTNAME/")
[ "$code" != 200 ] && pass "self-registration closed ($code)" || fail "self-registration page answered 200"

# 6. Postgres is not reachable from avedeus-edge (ADR 0007).
if docker run --rm --network avedeus-edge "$(docker inspect -f '{{.Config.Image}}' "$($ID ps -q postgres)")" \
     pg_isready -h postgres -t 3 >/dev/null 2>&1; then
  fail "Postgres is reachable from avedeus-edge"
else
  pass "Postgres not reachable from avedeus-edge"
fi

# 7. Backup format round-trips: the dump backup.sh takes lists its tables.
n=$($ID exec -T postgres pg_dump -U keycloak -d keycloak -Fc | $ID exec -T postgres pg_restore --list | grep -c ' TABLE DATA ' || true)
[ "${n:-0}" -gt 50 ] && pass "pg_dump archive lists $n tables" || fail "pg_dump archive lists ${n:-0} tables"

# Memory after start-up (gate condition 2 needs this measured on the real box).
docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' \
  "$($ID ps -q keycloak)" "$($ID ps -q postgres)" | sed 's/^/memory: /'

[ "$fails" -eq 0 ] && echo "identity smoke test passed" || echo "identity smoke test: $fails check(s) failed"
exit "$fails"
