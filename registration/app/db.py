"""SQLite storage for invitations and the event log.

Only invitation state lives here. Mailboxes live in mailcow and are created
through its API; this database never holds mailbox passwords.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS invitations (
    id               INTEGER PRIMARY KEY,
    token_hash       TEXT NOT NULL UNIQUE,      -- sha256 of the secret token; the token itself is never stored
    hint             TEXT NOT NULL,             -- first 6 chars of the token, to recognise it in the admin list
    note             TEXT NOT NULL DEFAULT '',
    quota_mb         INTEGER NOT NULL,
    created_at       INTEGER NOT NULL,
    expires_at       INTEGER NOT NULL,
    revoked_at       INTEGER,
    claimed_at       INTEGER,                   -- set while a registration is being provisioned
    redeemed_at      INTEGER,
    redeemed_address TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY,
    at            INTEGER NOT NULL,
    kind          TEXT NOT NULL,                -- invite_created, invite_revoked, invite_extended,
                                                -- registered, provision_failed, admin_login, admin_login_failed
    invitation_id INTEGER REFERENCES invitations(id),
    address       TEXT,
    detail        TEXT NOT NULL DEFAULT '',
    ip            TEXT
);
CREATE INDEX IF NOT EXISTS events_kind_at ON events(kind, at);
"""


class Database:
    def __init__(self, path: str, clock=time.time):
        self.path = path
        self.clock = clock
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def now(self) -> int:
        return int(self.clock())

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def log(self, kind: str, *, invitation_id=None, address=None, detail="", ip=None) -> None:
        self.execute(
            "INSERT INTO events(at, kind, invitation_id, address, detail, ip) VALUES (?,?,?,?,?,?)",
            (self.now(), kind, invitation_id, address, detail[:2000], ip),
        )

    def backup_to(self, dest: str) -> None:
        """Consistent online copy (used by scripts/backup.sh via the CLI)."""
        target = sqlite3.connect(dest)
        with self._lock:
            self._conn.backup(target)
        target.close()

    def close(self) -> None:
        self._conn.close()
