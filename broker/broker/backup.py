"""Consistent online copy of the control-plane database, for scripts/backup.sh.

    docker compose exec -T broker python -m broker.backup /data/backup.sqlite3
"""

import sqlite3
import sys

from .config import load_settings


def main() -> int:
    dest = sys.argv[1] if len(sys.argv) > 1 else "/data/backup.sqlite3"
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
