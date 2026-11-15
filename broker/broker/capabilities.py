"""Which caller may use which operation: the enforced copy of the
`enforced: true` rows in contracts/capabilities.yaml.

It is a Python copy (not read from the YAML) so the image needs nothing from
outside broker/. CI (contracts/tests) fails if the two ever differ.
"""

PUBLIC = frozenset({"healthz"})

CAPABILITIES: dict[str, frozenset[str]] = {
    "signup": frozenset({"checkAddress", "createMailbox"}),
    "monitoring": frozenset({"upstreamCheck", "health"}),
}
