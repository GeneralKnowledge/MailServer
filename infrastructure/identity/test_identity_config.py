"""Static checks for the identity stack (PR 11), run in CI's Contracts job.

These hold the Keycloak + Postgres configuration to the decisions it
implements, so a later edit can't quietly undo one:

* ADR 0006: multi-arch images pinned by version, memory limits, capped JVM
  heap, served at /auth/ through mailcow's nginx, admin console restricted.
* ADR 0007: Postgres is Keycloak's own and is not reachable from avedeus-edge.
* ADR 0015: browser identity only. No client allows the password grant, so
  nothing can turn Keycloak into a mail-client password check.
* ADR 0003: only the control plane's client may change users; mailcow's may
  only read them (for its own user sync).
* No secret is committed: the realm file only holds ${...} placeholders.

The start-and-import smoke test (CI job "Identity stack") proves the same
files actually start and import on a real Keycloak.
"""

import json
import re
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPOSE = yaml.safe_load((HERE / "docker-compose.yml").read_text())
REALM = json.loads((HERE / "realm" / "avedeus-realm.json").read_text())
NGINX = (ROOT / "infrastructure/mailcow/configuration/nginx/site.identity.custom").read_text()
ENV_EXAMPLE = (HERE / "identity.env.example").read_text()
SERVICES = COMPOSE["services"]


def _mb(limit: str) -> int:
    m = re.fullmatch(r"(\d+)([mg])", limit)
    assert m, limit
    return int(m.group(1)) * (1024 if m.group(2) == "g" else 1)


def test_images_pinned_to_a_version():
    for name, svc in SERVICES.items():
        image = svc["image"]
        tag = image.rsplit(":", 1)[-1]
        assert ":" in image and tag not in ("latest", "nightly"), name
        assert re.match(r"\d+\.\d+", tag), f"{name}: pin a full version, not {tag!r}"


def test_every_service_has_limits():
    for name, svc in SERVICES.items():
        assert "mem_limit" in svc, name
        assert "pids_limit" in svc, name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name
        assert "healthcheck" in svc, name


def test_no_ports_published():
    for name, svc in SERVICES.items():
        assert "ports" not in svc, f"{name} must only be reachable on its networks"


def test_jvm_heap_capped_below_container_limit():
    kc = SERVICES["keycloak"]
    heap = kc["environment"]["JAVA_OPTS_KC_HEAP"]
    xmx = re.search(r"-Xmx(\d+)m", heap)
    assert xmx, "Keycloak heap must be capped with -Xmx"
    # Leave room for metaspace, threads and buffers outside the heap.
    assert int(xmx.group(1)) <= _mb(kc["mem_limit"]) - 192


def test_postgres_is_keycloaks_own_and_not_on_the_edge_network():
    assert list(SERVICES["postgres"]["networks"]) == ["identity-db"]
    assert COMPOSE["networks"]["identity-db"]["internal"] is True
    # Nothing but Keycloak shares that network.
    on_db = [n for n, s in SERVICES.items() if "identity-db" in s["networks"]]
    assert sorted(on_db) == ["keycloak", "postgres"]


def test_keycloak_on_edge_at_its_fixed_address():
    edge = SERVICES["keycloak"]["networks"]["edge"]
    assert edge["ipv4_address"] == "172.30.240.12"   # .10 registration, .11 broker
    assert COMPOSE["networks"]["edge"] == {"external": True, "name": "avedeus-edge"}


def test_served_under_auth_behind_the_proxy():
    env = SERVICES["keycloak"]["environment"]
    assert env["KC_HTTP_RELATIVE_PATH"] == "/auth"
    assert env["KC_PROXY_HEADERS"] == "xforwarded"
    assert env["KC_METRICS_ENABLED"] == "false"      # PR 27 deferred
    assert "--import-realm" in SERVICES["keycloak"]["command"]
    assert SERVICES["keycloak"]["volumes"] == ["./realm:/opt/keycloak/data/import:ro"]


def test_nginx_proxies_auth_and_closes_admin_by_default():
    assert "location /auth/ {" in NGINX
    for loc in ("/auth/admin/", "/auth/admin/realms/avedeus/", "/auth/realms/master/", "/console/"):
        block = NGINX.split(f"location {loc} {{", 1)[1].split("\n}", 1)[0]
        active = [line.strip() for line in block.splitlines() if not line.strip().startswith("#")]
        assert "deny all;" in active, f"{loc} must fail closed"
        assert not any(line.startswith("allow ") for line in active), f"{loc} ships with no allow lines"
    # The management port (health, metrics) is never proxied.
    assert ":9000" not in NGINX


def test_realm_is_invite_only_and_hardened():
    assert REALM["realm"] == "avedeus"
    assert REALM["registrationAllowed"] is False      # accounts come from invitations
    assert REALM["sslRequired"] == "external"
    assert REALM["bruteForceProtected"] is True
    assert REALM["duplicateEmailsAllowed"] is False
    assert "length(12)" in REALM["passwordPolicy"]
    assert REALM["adminEventsDetailsEnabled"] is False  # admin events must not store representations


def test_mailcow_template_is_declared_and_admin_only():
    # Keycloak 24+ drops undeclared attributes, so the one PR 12 sets is declared.
    import json as _json
    cfg = REALM["components"]["org.keycloak.userprofile.UserProfileProvider"][0]["config"]["kc.user.profile.config"][0]
    attrs = {a["name"]: a for a in _json.loads(cfg)["attributes"]}
    assert attrs["mailcow_template"]["permissions"] == {"view": ["admin"], "edit": ["admin"]}
    assert {"username", "email", "firstName", "lastName"} <= set(attrs)


def test_users_cannot_change_their_email_or_username():
    # PR 17: mailcow finds a Keycloak user by email (= the mailbox address), so
    # a user editing their own email in the account console could point it at
    # another address. Admin-only; names stay the user's. Also checked live.
    import json as _json
    cfg = REALM["components"]["org.keycloak.userprofile.UserProfileProvider"][0]["config"]["kc.user.profile.config"][0]
    attrs = {a["name"]: a for a in _json.loads(cfg)["attributes"]}
    assert attrs["email"]["permissions"]["edit"] == ["admin"]
    assert attrs["username"]["permissions"]["edit"] == ["admin"]
    assert REALM["editUsernameAllowed"] is False
    assert "user" in attrs["firstName"]["permissions"]["edit"]


def test_groups_as_code():
    assert {g["name"] for g in REALM["groups"]} == {"users", "admins"}


def test_no_client_allows_password_grant_or_implicit_flow():
    # ADR 0015: Keycloak is browser identity only; mail clients use app passwords.
    for c in REALM["clients"]:
        assert c["directAccessGrantsEnabled"] is False, c["clientId"]
        assert c["implicitFlowEnabled"] is False, c["clientId"]
        assert c["publicClient"] is False, c["clientId"]


def test_only_the_control_plane_may_change_users():
    roles = {u["serviceAccountClientId"]: set(u["clientRoles"]["realm-management"]) for u in REALM["users"]}
    assert set(roles) == {"mailcow", "avedeus-control-plane"}
    writes = {"manage-users", "manage-realm", "manage-clients", "realm-admin", "manage-authorization",
              "manage-identity-providers", "manage-events"}
    assert not roles["mailcow"] & writes, "mailcow's client reads users for its sync, nothing more"
    assert roles["avedeus-control-plane"] & writes == {"manage-users"}
    # Only service-account users are defined in code; people come from invitations.
    assert all(u["username"].startswith("service-account-") for u in REALM["users"])


def test_mailcow_client_redirects_only_to_the_mail_host():
    mc = next(c for c in REALM["clients"] if c["clientId"] == "mailcow")
    assert mc["redirectUris"] == ["https://${MAIL_HOSTNAME}/*"]
    assert mc["attributes"]["pkce.code.challenge.method"] == "S256"
    cp = next(c for c in REALM["clients"] if c["clientId"] == "avedeus-control-plane")
    assert cp["standardFlowEnabled"] is False and cp["redirectUris"] == []


def test_portal_client_is_browser_only_with_an_exact_redirect():
    p = next(c for c in REALM["clients"] if c["clientId"] == "avedeus-portal")
    assert p["redirectUris"] == ["https://${MAIL_HOSTNAME}/account/callback"]   # exact, no wildcard
    assert p["standardFlowEnabled"] and not p["serviceAccountsEnabled"]
    assert p["attributes"]["pkce.code.challenge.method"] == "S256"
    assert "kc-admin-api" not in p["defaultClientScopes"]


def test_admin_console_client_always_asks_for_password_and_one_time_code():
    """PR 15 (ADR 0020): the console's own browser flow has no cookie shortcut
    and no alternative: password, then OTP, both REQUIRED; its tokens carry
    groups and amr, which the control plane checks."""
    a = next(c for c in REALM["clients"] if c["clientId"] == "avedeus-admin")
    assert a["redirectUris"] == ["https://${MAIL_HOSTNAME}/console/callback"]
    assert a["standardFlowEnabled"] and not a["serviceAccountsEnabled"] and not a["fullScopeAllowed"]
    assert a["attributes"]["pkce.code.challenge.method"] == "S256"
    flow = next(f for f in REALM["authenticationFlows"] if f["id"] == a["authenticationFlowBindingOverrides"]["browser"])
    steps = [(e["authenticator"], e["requirement"]) for e in flow["authenticationExecutions"]]
    assert steps == [("auth-username-password-form", "REQUIRED"), ("auth-otp-form", "REQUIRED")]
    configs = {c["alias"]: c["config"]["default.reference.value"] for c in REALM["authenticatorConfig"]}
    assert [configs[e["authenticatorConfig"]] for e in flow["authenticationExecutions"]] == ["pwd", "otp"]
    mappers = {m["protocolMapper"]: m["config"] for m in a["protocolMappers"]}
    for kind in ("oidc-group-membership-mapper", "oidc-amr-mapper"):
        assert mappers[kind]["access.token.claim"] == "true" and mappers[kind]["id.token.claim"] == "true"
    # Only the console's client uses this flow; everyone else keeps the realm's default.
    assert [c["clientId"] for c in REALM["clients"] if c.get("authenticationFlowBindingOverrides")] == ["avedeus-admin"]


def test_no_secrets_committed():
    for c in REALM["clients"]:
        assert re.fullmatch(r"\$\{[A-Z_]+\}", c["secret"]), f"{c['clientId']}: secret must be a placeholder"
    placeholders = set(re.findall(r"\$\{([A-Z_]+)\}", json.dumps(REALM)))
    # Every placeholder is documented in the env example.
    for name in placeholders:
        assert re.search(rf"^{name}=", ENV_EXAMPLE, re.M), f"{name} missing from identity.env.example"
    assert "identity.env\n" in (ROOT / ".gitignore").read_text().replace("infrastructure/identity/", "")
