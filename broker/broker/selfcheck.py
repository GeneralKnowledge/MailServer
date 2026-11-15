"""Check that the broker's mailcow key and allow-list work. Used by health-check.sh:

    docker compose exec -T broker python -m broker.selfcheck

Exit 0 and print the mailcow version, or exit 1 with the reason.
"""

import sys

from .config import load_settings
from .mailcow import MailcowClient, MailcowError


def main() -> int:
    s = load_settings()
    mc = MailcowClient(s.mailcow_api_url, s.mailcow_api_key, s.mailcow_tls_hostname, s.mailcow_verify_tls)
    try:
        print(f"mailcow API ok, version {mc.version()}")
        return 0
    except MailcowError as e:
        print(f"mailcow API check failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
