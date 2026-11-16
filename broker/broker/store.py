"""The control plane's own database (ADR 0007): the audit log, idempotency records,
identity links (mailbox address <-> Keycloak user id, ADR 0004; PR 12) and
scheduled account deletions (PR 16, ADR 0021) and the networks each person
has signed in from (PR 26).

SQLite in the broker's own volume. The audit table is append-only: triggers
refuse UPDATE and DELETE, so an event can't be edited or removed through
this code or an ordinary SQL client. Events follow
contracts/audit-event.schema.json and never hold passwords, tokens or
message content.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    address TEXT,
    event TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_action_ts ON audit_events(action, outcome, ts);
CREATE INDEX IF NOT EXISTS audit_address ON audit_events(address, seq);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;

CREATE TABLE IF NOT EXISTS idempotency (
    caller TEXT NOT NULL,
    key TEXT NOT NULL,
    operation TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    state TEXT NOT NULL,          -- pending | done
    status INTEGER,
    body TEXT,
    ts REAL NOT NULL,
    PRIMARY KEY (caller, key)
);

-- PR 12: which identity-provider user a mailbox belongs to. Links only:
-- Keycloak owns the identity, mailcow owns the mailbox (ADR 0004).
CREATE TABLE IF NOT EXISTS identity_links (
    address TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    ts REAL NOT NULL
);

-- PR 16: accounts suspended for deletion. The deletion job (broker.purge)
-- removes them after due_at, and only once a backup that started after
-- scheduled_at has completed (ADR 0021). A row is removed when the deletion
-- is cancelled or done; the history is in the audit log.
CREATE TABLE IF NOT EXISTS deletions (
    address TEXT PRIMARY KEY,
    scheduled_at REAL NOT NULL,
    due_at REAL NOT NULL,
    prior_state TEXT NOT NULL
);

-- PR 26: networks (IPv4 /24, IPv6 /48) each Keycloak user has signed in
-- from, so the notification job mails only about a new one. Rows older than
-- PLACE_KEEP_SECONDS are forgotten.
CREATE TABLE IF NOT EXISTS signin_places (
    subject TEXT NOT NULL,
    place TEXT NOT NULL,
    first_ts REAL NOT NULL,
    last_ts REAL NOT NULL,
    PRIMARY KEY (subject, place)
);
"""

PLACE_KEEP_SECONDS = 180 * 86400

IDEMPOTENCY_KEEP_SECONDS = 7 * 86400


class Store:
    def __init__(self, path: str, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.prune_idempotency()

    # ---- health ------------------------------------------------------------

    def health(self) -> dict:
        """Can we still write? Takes the write lock and lets it go, changing nothing."""
        try:
            with self.lock:
                self.db.execute("BEGIN IMMEDIATE")
                self.db.execute("ROLLBACK")
            return {"status": "ok"}
        except sqlite3.Error as e:
            return {"status": "down", "detail": f"database not writable: {type(e).__name__}"}

    # ---- audit -------------------------------------------------------------

    def audit(self, *, caller: str, action: str, outcome: str, request_id: str = "",
              operation: str | None = None, actor: dict | None = None, source_ip: str | None = None,
              address: str | None = None, error_kind: str | None = None, adapter: str | None = None,
              idempotency_key: str | None = None, changes: dict | None = None,
              counts: dict | None = None) -> dict:
        now = self.clock()
        event = {
            "schema": 1,
            "id": uuid.uuid4().hex,
            "time": datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z"),
            "request_id": request_id or uuid.uuid4().hex[:16],
            "caller": caller,
            "actor": actor,
            "source_ip": source_ip,
            "action": action,
            "operation": operation,
            "subject": {"address": address} if address else None,
            "outcome": outcome,
            "error_kind": error_kind,
            "adapter": adapter,
            "idempotency_key": idempotency_key,
        }
        if changes:
            event["changes"] = changes
        if counts:
            event["counts"] = counts
        with self.lock:
            self.db.execute("INSERT INTO audit_events (ts, action, outcome, address, event) VALUES (?,?,?,?,?)",
                            (now, action, outcome, address, json.dumps(event, separators=(",", ":"))))
        return event

    def count(self, action: str, outcome: str, since: float) -> int:
        with self.lock:
            return self.db.execute("SELECT count(*) FROM audit_events WHERE action=? AND outcome=? AND ts>?",
                                   (action, outcome, since)).fetchone()[0]

    def addresses(self, action: str, outcome: str) -> set[str]:
        with self.lock:
            rows = self.db.execute("SELECT DISTINCT address FROM audit_events WHERE action=? AND outcome=? "
                                   "AND address IS NOT NULL", (action, outcome)).fetchall()
        return {r[0] for r in rows}

    def last_seen(self, action: str, outcome: str) -> dict[str, int]:
        """address -> sequence number of its newest event with this action and outcome."""
        with self.lock:
            rows = self.db.execute("SELECT lower(address), max(seq) FROM audit_events WHERE action=? AND outcome=? "
                                   "AND address IS NOT NULL GROUP BY lower(address)", (action, outcome)).fetchall()
        return dict(rows)

    def latest(self, action: str, outcome: str = "ok") -> dict | None:
        """The newest event with this action and outcome."""
        with self.lock:
            row = self.db.execute("SELECT event FROM audit_events WHERE action=? AND outcome=? ORDER BY seq DESC LIMIT 1",
                                  (action, outcome)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, address: str, limit: int = 50) -> list[dict]:
        """Recent events about one address, newest first."""
        with self.lock:
            rows = self.db.execute("SELECT event FROM audit_events WHERE address=? ORDER BY seq DESC LIMIT ?",
                                   (address.lower(), limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def events(self, limit: int = 1000) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT event FROM audit_events ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    # ---- idempotency -------------------------------------------------------

    @staticmethod
    def fingerprint(body: dict, secret_fields: tuple[str, ...] = ("password",)) -> str:
        """Hash of the request without its secrets, so none are stored."""
        clean = {k: v for k, v in body.items() if k not in secret_fields}
        return hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()

    def idem_begin(self, caller: str, key: str, operation: str, fingerprint: str):
        """Returns None if the caller should go ahead, else ('replay', status, body) or ('conflict', reason)."""
        with self.lock:
            try:
                self.db.execute("INSERT INTO idempotency (caller, key, operation, fingerprint, state, ts) "
                                "VALUES (?,?,?,?, 'pending', ?)", (caller, key, operation, fingerprint, self.clock()))
                return None
            except sqlite3.IntegrityError:
                row = self.db.execute("SELECT operation, fingerprint, state, status, body FROM idempotency "
                                      "WHERE caller=? AND key=?", (caller, key)).fetchone()
        op, fp, state, status, body = row
        if op != operation or fp != fingerprint:
            return ("conflict", "Idempotency-Key reused with a different request")
        if state != "done":
            return ("conflict", "a request with this Idempotency-Key is still in progress")
        return ("replay", status, json.loads(body))

    def idem_finish(self, caller: str, key: str, status: int, body: dict) -> None:
        with self.lock:
            self.db.execute("UPDATE idempotency SET state='done', status=?, body=? WHERE caller=? AND key=?",
                            (status, json.dumps(body), caller, key))

    def idem_abandon(self, caller: str, key: str) -> None:
        """A failed attempt leaves no record, so a retry with the same key runs again."""
        with self.lock:
            self.db.execute("DELETE FROM idempotency WHERE caller=? AND key=?", (caller, key))

    def prune_idempotency(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM idempotency WHERE ts < ?", (self.clock() - IDEMPOTENCY_KEEP_SECONDS,))

    # ---- identity links (PR 12) ----------------------------------------------

    def link_set(self, address: str, provider: str, subject: str) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO identity_links (address, provider, subject, ts) VALUES (?,?,?,?)",
                            (address.lower(), provider, subject, self.clock()))

    def link_get(self, address: str, provider: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT subject FROM identity_links WHERE address=? AND provider=?",
                                  (address.lower(), provider)).fetchone()
        return row[0] if row else None

    def link_delete(self, address: str, provider: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM identity_links WHERE address=? AND provider=?", (address.lower(), provider))

    def address_for(self, provider: str, subject: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT address FROM identity_links WHERE provider=? AND subject=?",
                                  (provider, subject)).fetchone()
        return row[0] if row else None

    def links(self, provider: str) -> dict[str, str]:
        with self.lock:
            rows = self.db.execute("SELECT address, subject FROM identity_links WHERE provider=?", (provider,)).fetchall()
        return dict(rows)

    # ---- scheduled deletions (PR 16) -------------------------------------------

    def deletion_add(self, address: str, due_at: float, prior_state: str) -> bool:
        """False if one is already scheduled."""
        with self.lock:
            try:
                self.db.execute("INSERT INTO deletions (address, scheduled_at, due_at, prior_state) VALUES (?,?,?,?)",
                                (address.lower(), self.clock(), due_at, prior_state))
                return True
            except sqlite3.IntegrityError:
                return False

    def deletion_get(self, address: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT address, scheduled_at, due_at, prior_state FROM deletions WHERE address=?",
                                  (address.lower(),)).fetchone()
        return dict(zip(("address", "scheduled_at", "due_at", "prior_state"), row)) if row else None

    def deletion_remove(self, address: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM deletions WHERE address=?", (address.lower(),))

    def deletions(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT address, scheduled_at, due_at, prior_state FROM deletions "
                                   "ORDER BY due_at").fetchall()
        return [dict(zip(("address", "scheduled_at", "due_at", "prior_state"), r)) for r in rows]

    # ---- sign-in places (PR 26) ------------------------------------------------

    def places(self, subject: str) -> set[str]:
        with self.lock:
            rows = self.db.execute("SELECT place FROM signin_places WHERE subject=?", (subject,)).fetchall()
        return {r[0] for r in rows}

    def place_seen(self, subject: str, place: str, ts: float) -> None:
        with self.lock:
            self.db.execute("INSERT INTO signin_places (subject, place, first_ts, last_ts) VALUES (?,?,?,?) "
                            "ON CONFLICT(subject, place) DO UPDATE SET last_ts=max(last_ts, excluded.last_ts)",
                            (subject, place, ts, ts))

    def places_forget(self, subject: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM signin_places WHERE subject=?", (subject,))

    def prune_places(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM signin_places WHERE last_ts < ?", (self.clock() - PLACE_KEEP_SECONDS,))
