"""Command-line entrypoint for local / manual runs:  python -m app.cli"""

from __future__ import annotations

import json
import sys

from .config import get_settings
from .legifrance import LegifranceClient
from .logging_config import setup_logging
from .monitor import run_scan
from .storage import get_storage


def main() -> int:
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.names:
        print("SEARCH_NAMES is empty -- set it in your .env file.", file=sys.stderr)
        return 2
    if not (settings.piste_client_id and settings.piste_client_secret):
        print("Missing PISTE_CLIENT_ID / PISTE_CLIENT_SECRET in .env.", file=sys.stderr)
        return 2

    storage = get_storage(settings)
    client = LegifranceClient(settings)
    summary = run_scan(settings, storage, client)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
