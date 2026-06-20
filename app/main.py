"""
FastAPI web service -- the deployable entrypoint.

Endpoints
---------
GET  /          -> service info
GET  /healthz   -> liveness probe
GET  /status    -> last run summary, backend stats, recent matches
POST /scan      -> trigger a scan (token-protected); runs in a background
                   thread and returns 202 immediately.

Scheduling
----------
* On an always-on host (VPS / Docker), the built-in scheduler triggers scans on
  an interval (SCHEDULER_ENABLED=true) -- no external cron needed.
* On Render free (spins down when idle), set SCHEDULER_ENABLED=false and use the
  external GitHub Actions trigger to POST /scan.

Run with a single worker so only one scheduler exists.
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from contextlib import asynccontextmanager
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

# Single-flight guard: only one scan at a time (the scan is long-running).
_scan_lock = threading.Lock()
_run_state: dict = {"running": False, "last_run_at": None, "last_summary": None, "last_error": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def start_scan() -> bool:
    """Start a scan if none is running. Returns True if started, False if busy."""
    if not _scan_lock.acquire(blocking=False):
        return False
    _run_state["running"] = True
    threading.Thread(target=_run_scan_thread, name="scan", daemon=True).start()
    return True


def _scheduler_loop() -> None:
    interval = max(60, settings.scan_interval_minutes * 60)
    if settings.scan_on_startup:
        time.sleep(5)  # let the server settle before the first run
        if start_scan():
            log.info("Startup scan started.")
    while True:
        time.sleep(interval)
        if start_scan():
            log.info("Scheduled scan started.")
        else:
            log.info("Scheduled tick skipped -- a scan is already running.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.scheduler_enabled:
        threading.Thread(target=_scheduler_loop, name="scheduler", daemon=True).start()
        log.info("Internal scheduler ON: every %d min (on_startup=%s).",
                 settings.scan_interval_minutes, settings.scan_on_startup)
    else:
        log.info("Internal scheduler OFF -- trigger scans via POST /scan.")
    yield


app = FastAPI(title="Legifrance JO Monitor", version=__version__, lifespan=lifespan)


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


@app.get("/")
def root() -> dict:
    return {
        "service": "legifrance-jo-monitor",
        "version": __version__,
        "watching": settings.names,
        "email_enabled": settings.email_enabled,
        "scheduler_enabled": settings.scheduler_enabled,
        "scan_interval_minutes": settings.scan_interval_minutes,
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

    if not start_scan():
        return JSONResponse(status_code=409,
                            content={"status": "already_running", "since": _run_state["last_run_at"]})

    log.info("Scan triggered via API by %s.", request.client.host if request.client else "?")
    return JSONResponse(status_code=202, content={"status": "started"})
