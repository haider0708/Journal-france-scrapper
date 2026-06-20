"""
FastAPI web service -- the deployable entrypoint (Render free tier).

Endpoints
---------
GET  /          -> service info
GET  /healthz   -> liveness probe (used by Render health check)
GET  /status    -> last run summary, backend stats, recent matches
POST /scan      -> trigger a scan (token-protected); runs in a background
                   thread and returns 202 immediately so the external cron
                   caller never waits/times out.

Because Render free web services spin down when idle and can't run cron jobs,
an EXTERNAL scheduler (GitHub Actions / cron-job.org) must hit POST /scan on a
schedule. See README.
"""

from __future__ import annotations

import hmac
import logging
import threading
from datetime import datetime, timezone

from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import get_settings
from .legifrance import LegifranceClient
from .logging_config import setup_logging
from .monitor import run_scan
from .storage import get_storage

settings = get_settings()
setup_logging(settings.log_level)
log = logging.getLogger("legifrance.web")

storage = get_storage(settings)

app = FastAPI(title="Legifrance JO Monitor", version=__version__)

# Single-flight guard: only one scan at a time (the scan is long-running).
_scan_lock = threading.Lock()
_run_state: dict = {"running": False, "last_run_at": None, "last_summary": None, "last_error": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _authorized(authorization: str | None, token: str | None) -> bool:
    """Constant-time check of the Bearer header or ?token= against CRON_SECRET."""
    if not settings.cron_secret:
        return False
    presented = ""
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    elif token:
        presented = token.strip()
    return bool(presented) and hmac.compare_digest(presented, settings.cron_secret)


def _run_scan_thread() -> None:
    try:
        client = LegifranceClient(settings)
        summary = run_scan(settings, storage, client)
        _run_state["last_summary"] = summary
        _run_state["last_error"] = None
    except Exception as exc:  # noqa: BLE001 - record and keep service alive
        log.exception("Scan failed.")
        _run_state["last_error"] = str(exc)
    finally:
        _run_state["running"] = False
        _run_state["last_run_at"] = _now()
        _scan_lock.release()


@app.get("/")
def root() -> dict:
    return {
        "service": "legifrance-jo-monitor",
        "version": __version__,
        "watching": settings.names,
        "email_enabled": settings.email_enabled,
        "endpoints": ["/healthz", "/status", "POST /scan"],
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/status")
def status() -> dict:
    return {
        "running": _run_state["running"],
        "last_run_at": _run_state["last_run_at"],
        "last_summary": _run_state["last_summary"],
        "last_error": _run_state["last_error"],
        "storage": storage.stats(),
        "recent_matches": storage.recent_matches(limit=20),
    }


@app.post("/scan")
def scan(
    request: Request,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> JSONResponse:
    if not settings.cron_secret:
        return JSONResponse(status_code=503,
                            content={"error": "CRON_SECRET not configured on server."})
    if not _authorized(authorization, token):
        return JSONResponse(status_code=401, content={"error": "Unauthorized."})

    # Non-blocking acquire: if a scan is already running, don't queue another.
    if not _scan_lock.acquire(blocking=False):
        return JSONResponse(status_code=409,
                            content={"status": "already_running", "since": _run_state["last_run_at"]})

    _run_state["running"] = True
    threading.Thread(target=_run_scan_thread, name="scan", daemon=True).start()
    log.info("Scan triggered by %s.", request.client.host if request.client else "?")
    return JSONResponse(status_code=202, content={"status": "started"})
