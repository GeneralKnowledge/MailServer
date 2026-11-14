"""Invitation lifecycle: create, check, claim, redeem, release, revoke, extend.

Redemption is two-phase so that a failed mailcow call never uses up an
invitation and two people can't redeem one invitation at the same time:

1. ``claim``  atomically marks a valid invitation as "being provisioned".
2. The mailbox is created through the mailcow API.
3. ``redeem`` on success, or ``release`` on failure.

A claim left behind by a crash expires after CLAIM_TIMEOUT seconds.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from .db import Database

CLAIM_TIMEOUT = 300
TOKEN_BYTES = 24  # 192 bits -> 32 url-safe characters


def hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


@dataclass
class Invitation:
    id: int
    hint: str
    note: str
    quota_mb: int
    created_at: int
    expires_at: int
    revoked_at: int | None
    claimed_at: int | None
    redeemed_at: int | None
    redeemed_address: str | None
    status: str  # valid | in_progress | redeemed | revoked | expired

    @property
    def usable(self) -> bool:
        return self.status == "valid"


def _status(row, now: int) -> str:
    if row["redeemed_at"]:
        return "redeemed"
    if row["revoked_at"]:
        return "revoked"
    if row["expires_at"] <= now:
        return "expired"
    if row["claimed_at"] and row["claimed_at"] > now - CLAIM_TIMEOUT:
        return "in_progress"
    return "valid"


def _to_inv(row, now: int) -> Invitation:
    return Invitation(
        id=row["id"], hint=row["hint"], note=row["note"], quota_mb=row["quota_mb"],
        created_at=row["created_at"], expires_at=row["expires_at"], revoked_at=row["revoked_at"],
        claimed_at=row["claimed_at"], redeemed_at=row["redeemed_at"],
        redeemed_address=row["redeemed_address"], status=_status(row, now),
    )


class Invitations:
    def __init__(self, db: Database):
        self.db = db

    def create(self, *, quota_mb: int, days: int, note: str = "", ip: str | None = None) -> tuple[str, Invitation]:
        """Returns (secret token, invitation). The token is shown once and never stored."""
        if days < 1 or days > 90:
            raise ValueError("Expiry must be between 1 and 90 days")
        if quota_mb < 1:
            raise ValueError("Quota must be positive")
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = self.db.now()
        cur = self.db.execute(
            "INSERT INTO invitations(token_hash, hint, note, quota_mb, created_at, expires_at) VALUES (?,?,?,?,?,?)",
            (hash_token(token), token[:6], note.strip()[:200], quota_mb, now, now + days * 86400),
        )
        self.db.log("invite_created", invitation_id=cur.lastrowid, detail=f"quota={quota_mb}MB days={days}", ip=ip)
        return token, self.get(cur.lastrowid)

    def get(self, inv_id: int) -> Invitation | None:
        row = self.db.one("SELECT * FROM invitations WHERE id=?", (inv_id,))
        return _to_inv(row, self.db.now()) if row else None

    def find(self, token: str) -> Invitation | None:
        if not token or len(token) > 100:
            return None
        h = hash_token(token)
        row = self.db.one("SELECT * FROM invitations WHERE token_hash=?", (h,))
        if not row or not hmac.compare_digest(row["token_hash"], h):
            return None
        return _to_inv(row, self.db.now())

    def claim(self, inv_id: int) -> bool:
        now = self.db.now()
        cur = self.db.execute(
            """UPDATE invitations SET claimed_at=?
               WHERE id=? AND redeemed_at IS NULL AND revoked_at IS NULL AND expires_at>?
                 AND (claimed_at IS NULL OR claimed_at<=?)""",
            (now, inv_id, now, now - CLAIM_TIMEOUT),
        )
        return cur.rowcount == 1

    def release(self, inv_id: int) -> None:
        self.db.execute("UPDATE invitations SET claimed_at=NULL WHERE id=? AND redeemed_at IS NULL", (inv_id,))

    def redeem(self, inv_id: int, address: str) -> None:
        self.db.execute(
            "UPDATE invitations SET redeemed_at=?, redeemed_address=?, claimed_at=NULL WHERE id=?",
            (self.db.now(), address, inv_id),
        )

    def revoke(self, inv_id: int, ip: str | None = None) -> bool:
        cur = self.db.execute(
            "UPDATE invitations SET revoked_at=? WHERE id=? AND redeemed_at IS NULL AND revoked_at IS NULL",
            (self.db.now(), inv_id),
        )
        if cur.rowcount:
            self.db.log("invite_revoked", invitation_id=inv_id, ip=ip)
        return cur.rowcount == 1

    def extend(self, inv_id: int, days: int, ip: str | None = None) -> bool:
        """Set expiry to now + days (also revives an expired, unused invitation)."""
        if days < 1 or days > 90:
            raise ValueError("Expiry must be between 1 and 90 days")
        cur = self.db.execute(
            "UPDATE invitations SET expires_at=? WHERE id=? AND redeemed_at IS NULL AND revoked_at IS NULL",
            (self.db.now() + days * 86400, inv_id),
        )
        if cur.rowcount:
            self.db.log("invite_extended", invitation_id=inv_id, detail=f"days={days}", ip=ip)
        return cur.rowcount == 1

    def list(self, limit: int = 500) -> list[Invitation]:
        now = self.db.now()
        rows = self.db.query("SELECT * FROM invitations ORDER BY id DESC LIMIT ?", (limit,))
        return [_to_inv(r, now) for r in rows]
