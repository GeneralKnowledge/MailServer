"""Backup helpers for scripts/backup.sh.

Consistent online copy of the control-plane database:

    docker compose exec -T broker python -m broker.backup /data/backup.sqlite3

Record that a backup run finished, verified and (if configured) uploaded
(PR 16): one `backup.completed` audit event with the time the run started.
The deletion job removes an account only after a backup that started after
its deletion was scheduled (ADR 0021).

    docker compose exec -T broker python -m broker.backup --mark-complete <started epoch seconds>
"""

import sqlite3
import sys
import time

from .config import load_settings
from .store import Store


def mark_complete(store: Store, started: int, now: float) -> None:
    if not 0 < started <= now:
        raise ValueError("the start time must be in the past")
    store.audit(caller="system", action="backup.completed", outcome="ok", counts={"started_at": int(started)})


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--mark-complete"]:
        try:
            started = int(args[1])
            mark_complete(Store(load_settings().db_path), started, time.time())
        except (IndexError, ValueError) as e:
            print(f"usage: python -m broker.backup --mark-complete <started epoch seconds> ({e})")
            return 2
        print(f"backup recorded as complete (started {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(started))})")
        return 0
    dest = args[0] if args else "/data/backup.sqlite3"
    src = sqlite3.connect(load_settings().db_path)
    out = sqlite3.connect(dest)
    with out:
        src.backup(out)
    n = out.execute("SELECT count(*) FROM audit_events").fetchone()[0]
    out.close()
    src.close()
    print(f"control-plane database copied to {dest} ({n} audit events)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
