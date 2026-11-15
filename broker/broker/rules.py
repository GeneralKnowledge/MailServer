"""Address rules the broker enforces whatever the caller says."""

from __future__ import annotations

import re

_LOCAL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|[._-](?=[a-z0-9]))*$")
MAX_LOCAL_LEN = 32


def local_part_error(local: str, *, reserved: frozenset[str], min_len: int) -> str | None:
    if not (min_len <= len(local) <= MAX_LOCAL_LEN):
        return f"length must be {min_len}-{MAX_LOCAL_LEN}"
    if not _LOCAL_RE.match(local):
        return "only lowercase letters, digits and single . _ - between them"
    if local in reserved or re.sub(r"[._-]", "", local) in reserved:
        return "reserved"
    return None
