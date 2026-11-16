"""Which caller may use which operation: the enforced copy of the
`enforced: true` rows in contracts/capabilities.yaml.

It is a Python copy (not read from the YAML) so the image needs nothing from
outside broker/. CI (contracts/tests) fails if the two ever differ.
"""

PUBLIC = frozenset({"healthz"})

CAPABILITIES: dict[str, frozenset[str]] = {
    "signup": frozenset({"checkAddress", "createMailbox", "createAccount"}),
    "monitoring": frozenset({"upstreamCheck", "health"}),
    # PR 34: token + source IP for now; PR 15 adds the admin's Keycloak token, group and TOTP.
    "admin": frozenset({"traceMessage"}),
    # PR 14: the signed-in user's own account; the user comes from their verified Keycloak token.
    "portal": frozenset({"getOwnAccount"}),
}
