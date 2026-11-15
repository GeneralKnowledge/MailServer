"""Which caller may use which operation: the enforced copy of the
`enforced: true` rows in contracts/capabilities.yaml.

It is a Python copy (not read from the YAML) so the image needs nothing from
outside broker/. CI (contracts/tests) fails if the two ever differ.
"""

PUBLIC = frozenset({"healthz"})

CAPABILITIES: dict[str, frozenset[str]] = {
    "signup": frozenset({"checkAddress", "createMailbox", "createAccount"}),
    "monitoring": frozenset({"upstreamCheck", "health"}),
    # PR 34, PR 15, PR 16: token + source IP + the admin's own Keycloak token (avedeus-admin
    # client, admins group, second factor), except break-glass from 127.0.0.1 (ADR 0020).
    "admin": frozenset({"traceMessage", "listAccounts", "readAudit", "getAccount", "suspendAccount",
                        "reactivateAccount", "setAccountQuota", "forcePasswordReset", "scheduleDeletion",
                        "cancelDeletion"}),
    # PR 14: the signed-in user's own account; the user comes from their verified Keycloak token.
    "portal": frozenset({"getOwnAccount"}),
}
