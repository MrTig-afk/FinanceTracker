"""app.py — FastAPI backend for FinanceTracker (§7.2, FR-5..FR-7).

Endpoints
---------
POST /upload    Accept CommBank and/or Westpac CSVs as multipart form fields.
GET  /status    Health + last-run summary (no sensitive content).
GET  /summary   Monthly spending totals (latest or ?month=YYYY-MM).

Privacy contract
----------------
- Upload bytes are read in-memory only (``await file.read()``); raw CSV is NEVER
  written to a tracked path.
- HTTP error responses never echo raw transaction text, raw exception strings, or
  account numbers — only fixed safe strings or count/bool fields.
- GET /status returns only booleans for configured.drive and configured.openrouter;
  no API key value or service-account path is ever included.
- GET /summary returns the owner's own data to the owner's own localhost/Tailscale
  client — this is a local serve, not an off-machine send.

Config (all from .env via python-dotenv — never hardcoded)
-----------------------------------------------------------
BACKEND_HOST            bind address (default "0.0.0.0")
BACKEND_PORT            bind port (default 8000)
SQLITE_PATH             passed to Store; Store creates parents when create_parents=True
CORS_ALLOW_ORIGINS      optional comma-separated extra origins (add your tailnet origin here)
OPENROUTER_API_KEY      checked for bool only in /status; actual use is inside pipeline
GOOGLE_SERVICE_ACCOUNT_JSON / DRIVE_FOLDER_ID  checked by is_configured() in /status

Effective CORS origins are logged at startup (origins only, never secrets).
"""
from __future__ import annotations

import dataclasses
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from backend.data_source import Bank
from backend.drive_uploader import is_configured
from backend.pipeline import RunReport, UploadedFile, run_pipeline
from backend.store import Store

# ---------------------------------------------------------------------------
# Bootstrap — load .env once at module import (config read, no network/DB/file-create)
# ---------------------------------------------------------------------------

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (resolved from .env; defaults if not set)
# ---------------------------------------------------------------------------

BACKEND_HOST: str = os.getenv("BACKEND_HOST", "0.0.0.0")
BACKEND_PORT: int = int(os.getenv("BACKEND_PORT", "8000"))

_DEFAULT_ORIGINS: list[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
_extra_raw: str = os.getenv("CORS_ALLOW_ORIGINS", "")
_extra_origins: list[str] = [o.strip() for o in _extra_raw.split(",") if o.strip()]
CORS_ORIGINS: list[str] = _DEFAULT_ORIGINS + _extra_origins


# ---------------------------------------------------------------------------
# Lifespan (store wiring; no other startup IO)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Log effective CORS origins at startup — origins only, never secrets.
    logger.info("CORS allow_origins: %s", CORS_ORIGINS)

    # Open the single long-lived SQLite connection for the app's lifetime.
    # create_parents=True so ./data/ is created on first run (gitignored path).
    app.state.store = Store(create_parents=True)
    app.state.started_at = datetime.now(timezone.utc)
    app.state.last_report: RunReport | None = None
    app.state.last_run_at: datetime | None = None

    try:
        yield
    finally:
        app.state.store.close()


# ---------------------------------------------------------------------------
# App + CORS middleware
# ---------------------------------------------------------------------------

app = FastAPI(lifespan=lifespan, title="FinanceTracker")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# POST /upload  (FR-5)
# ---------------------------------------------------------------------------


@app.post("/upload")
async def upload(
    commbank: Annotated[UploadFile | None, File()] = None,
    westpac: Annotated[UploadFile | None, File()] = None,
):
    """Accept CommBank and/or Westpac CSV files and run the full pipeline.

    Multipart form fields:
        commbank   — optional; CommBank NetBank CSV
        westpac    — optional; Westpac CSV

    At least one non-empty field is required (400 otherwise).

    Returns 200 with RunReport JSON on success.  Parse failures for individual
    files are reported as safe strings in RunReport.errors — not as HTTP errors —
    because the request itself succeeded.  Only a truly unexpected crash is a 500.
    """
    uploads: list[UploadedFile] = []

    for file, bank in (
        (commbank, Bank.COMMBANK),
        (westpac, Bank.WESTPAC),
    ):
        if file is None:
            continue
        content = await file.read()
        if len(content) == 0:
            # Named field present but empty — caller error.
            raise HTTPException(
                status_code=400,
                detail=f"{bank.value} file is empty",
            )
        uploads.append(
            UploadedFile(
                filename=file.filename or f"{bank.value}.csv",
                bank=bank,
                content=content,  # in-memory only; NEVER written to a tracked path
            )
        )

    if not uploads:
        raise HTTPException(status_code=400, detail="no files uploaded")

    try:
        report = run_pipeline(uploads, store=app.state.store)
    except Exception:
        # Unexpected crash — log it server-side but return a safe generic message.
        logger.exception("run_pipeline raised an unexpected exception")
        raise HTTPException(status_code=500, detail="internal error")

    # Persist last run info for /status.
    app.state.last_report = report
    app.state.last_run_at = datetime.now(timezone.utc)

    return dataclasses.asdict(report)


# ---------------------------------------------------------------------------
# GET /status  (FR-7)
# ---------------------------------------------------------------------------


@app.get("/status")
async def status():
    """Return health and last-run summary.

    Designed to contain NO sensitive content:
    - ``configured.drive`` and ``configured.openrouter`` are booleans only.
    - ``last_run`` is a RunReport (counts, safe strings, model id) — no raw txn text.
    - Never includes API keys, service-account paths, or account numbers.
    """
    started_at: datetime = app.state.started_at
    uptime_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()

    last_run_at: datetime | None = app.state.last_run_at
    last_report: RunReport | None = app.state.last_report

    return {
        "status": "ok",
        "uptime_seconds": uptime_seconds,
        "last_run_at": last_run_at.isoformat() if last_run_at is not None else None,
        "last_run": dataclasses.asdict(last_report) if last_report is not None else None,
        "configured": {
            # Booleans only — never echo the key value or file path.
            "drive": is_configured(),
            "openrouter": bool(os.getenv("OPENROUTER_API_KEY")),
        },
    }


# ---------------------------------------------------------------------------
# GET /summary  (FR-32, FR-33)
# ---------------------------------------------------------------------------


@app.get("/summary")
async def summary(
    month: Annotated[str | None, Query(description="YYYY-MM")] = None,
):
    """Return categorised spending summary for a given month.

    Query params:
        month   — optional "YYYY-MM".  Omit to get the latest month.

    Returns the compact shape the dashboard pie reads:
        { "year_month": "YYYY-MM"|null, "totals": {...}, "net": "str", "count": N }

    Note: amounts are str(Decimal), never float.
    """
    if month is not None and not re.match(r"^\d{4}-\d{2}$", month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")

    return app.state.store.summary(month)


# ---------------------------------------------------------------------------
# Entrypoint (used by service/run-backend.ps1 and direct `python backend/app.py`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host=BACKEND_HOST,
        port=BACKEND_PORT,
    )
