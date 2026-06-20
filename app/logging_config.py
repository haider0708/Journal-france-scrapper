"""Logging setup -- logs to stdout (captured by Render's log stream)."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.addHandler(handler)
    _CONFIGURED = True
