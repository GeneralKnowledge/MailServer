"""The adapter boundary (ADR 0009, docs/integration-contract.md).

Deliberately small (ADR 0017): one Protocol and one health shape, and one
adapter per integration actually in use. Today that is only mailcow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Status = Literal["ok", "degraded", "down"]


@dataclass(frozen=True)
class Health:
    status: Status
    detail: str = ""
    latency_ms: int | None = None

    def as_dict(self) -> dict:
        d: dict = {"status": self.status}
        if self.detail:
            d["detail"] = self.detail[:500]
        if self.latency_ms is not None:
            d["latency_ms"] = self.latency_ms
        return d


class Adapter(Protocol):
    """What every adapter offers besides its own typed operations.

    Errors raised by an adapter carry a `kind` from the contract's ErrorKind
    (auth, unavailable, rejected, taken, password, ...) and a short detail
    without secrets.
    """

    name: str

    def health(self) -> Health:
        """At most one cheap upstream call."""
        ...
