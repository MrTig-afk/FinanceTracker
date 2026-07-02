"""app.py — FastAPI backend for FinanceTracker (§7.2, FR-5..FR-7).

Endpoints
---------
POST /upload    Accept CommBank and/or Westpac CSVs as multipart form fields.
GET  /status    Health + last-run summary (no sensitive content).
GET  /summary   Monthly spending totals (latest or ?month=YYYY-MM).
GET  /month     Monthly breakdown + month-over-month comparison (latest or ?ym=YYYY-MM).
GET  /year      Yearly breakdown + year-over-year comparison (latest or ?y=YYYY).
GET  /trends    Per-category spending across a window of recent months
                (?months=1-24, default 6; ?end=YYYY-MM, default latest month).
GET  /category-context  The 9 canonical categories with stored hints (D1/D2).
PUT  /category-context  Replace-all of the 9 canonical categories' hints.
POST /category-override Override one transaction's category + remember the correction.
POST /push/subscribe    Store a Web Push subscription locally (v2 Pass 3 scaffold).
POST /push/unsubscribe  Remove a stored Web Push subscription by endpoint.

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
- GET /month and GET /year are LOCAL, read-only aggregations of the same store —
  same local-serve posture as /summary. No new off-machine call.
- GET /trends is another LOCAL, read-only aggregation of the same store — same
  local-serve posture as /summary. No new off-machine call.
- POST /push/subscribe and /push/unsubscribe store/remove the caller's OWN device
  Web Push subscription in local SQLite only — no off-machine call here. The scaffold
  that WOULD later send a push (backend/notifier) is feature-flagged OFF by default
  (see backend/notifier for the fail-closed gate).

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
from pydantic import BaseModel, Field

from backend.data_source import Bank
from backend.drive_uploader import is_configured
from backend.pipeline import RunReport, UploadedFile, run_pipeline
from backend.store import Store, TAXONOMY

# The pure scrub helpers are reused (not the full sanitise() batch path) to clean a
# single raw description before it is stored as a reusable correction example. This
# is the ONLY sanctioned way a description-derived string enters the corrections
# table, and it fails closed: an un-sanitisable description is never stored.
from backend.sanitiser.scrub import has_residual_identifier, scrub_description

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
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic models — category-context PUT body
# ---------------------------------------------------------------------------


class CategoryHintsIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)   # must be a canonical taxonomy name
    hints: str = Field(default="", max_length=2000)


class CategoryContextIn(BaseModel):
    categories: list[CategoryHintsIn]


# ---------------------------------------------------------------------------
# Pydantic model — manual category override body
# ---------------------------------------------------------------------------


class CategoryOverrideIn(BaseModel):
    """Override one transaction's category by row id OR transaction fingerprint.

    Exactly one of id / fingerprint identifies the row; category must be a canonical
    taxonomy label (validated in the handler, not here, so junk yields a clean 400).
    """

    id: int | None = None
    fingerprint: str | None = Field(default=None, max_length=128)
    category: str = Field(min_length=1, max_length=60)


# ---------------------------------------------------------------------------
# Pydantic models — push subscription bodies (v2 Pass 3 — inert scaffold)
# ---------------------------------------------------------------------------


class PushKeysIn(BaseModel):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)
    keys: PushKeysIn


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)


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
# GET /category-transactions  (v2 — dashboard category drill-down)
# ---------------------------------------------------------------------------

# The 9 canonical categories plus the 'Uncategorised' bucket (NULL category),
# which the summary/donut surfaces as its own slice.
_DRILLDOWN_CATEGORIES: frozenset[str] = frozenset(TAXONOMY) | {"Uncategorised"}


@app.get("/category-transactions")
async def category_transactions(
    category: Annotated[str, Query(description="category label")],
    month: Annotated[str | None, Query(description="YYYY-MM")] = None,
):
    """Return one category's transactions for a month (dashboard drill-down).

    LOCAL, read-only view of the owner's own store — same local-serve posture as
    /summary. Descriptions are the owner's own data served to the owner's own
    localhost/Tailscale client; nothing here is ever sent off-machine.

    Query params:
        category — one of the 9 canonical categories, or 'Uncategorised'.
        month    — optional 'YYYY-MM'. Omit to get the latest month.
    """
    if category not in _DRILLDOWN_CATEGORIES:
        raise HTTPException(status_code=400, detail="unknown category")
    if month is not None and not re.match(r"^\d{4}-\d{2}$", month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")

    return app.state.store.transactions_for_category(category, month)


# ---------------------------------------------------------------------------
# GET /month  (v2 Pass 1 — monthly breakdown + month-over-month comparison)
# ---------------------------------------------------------------------------


@app.get("/month")
async def month(
    ym: Annotated[str | None, Query(description="YYYY-MM")] = None,
):
    """Return the monthly breakdown + month-over-month comparison for one month.

    Query params:
        ym   — optional "YYYY-MM". Omit to get the latest populated month.

    LOCAL, read-only aggregation of the owner's own store — same local-serve
    posture as /summary. Amounts are str(Decimal), never float.
    """
    if ym is not None and not re.match(r"^\d{4}-\d{2}$", ym):
        raise HTTPException(status_code=400, detail="ym must be YYYY-MM")

    return app.state.store.month_view(ym)


# ---------------------------------------------------------------------------
# GET /year  (v2 Pass 1 — yearly breakdown + year-over-year comparison)
# ---------------------------------------------------------------------------


@app.get("/year")
async def year(
    y: Annotated[str | None, Query(description="YYYY")] = None,
):
    """Return the yearly breakdown + year-over-year comparison for one year.

    Query params:
        y   — optional "YYYY". Omit to get the latest populated year.

    LOCAL, read-only aggregation of the owner's own store — same local-serve
    posture as /summary. Amounts are str(Decimal), never float.
    """
    if y is not None and not re.match(r"^\d{4}$", y):
        raise HTTPException(status_code=400, detail="y must be YYYY")

    return app.state.store.year_view(y)


# ---------------------------------------------------------------------------
# GET /trends  (v2 Pass 2 — category spending across a window of recent months)
# ---------------------------------------------------------------------------


@app.get("/trends")
async def trends(
    months: Annotated[int, Query(description="window size in months (1-24)")] = 6,
    end: Annotated[str | None, Query(description="YYYY-MM window end")] = None,
):
    """Return per-category spending across a window of recent months (LOCAL, read-only).

    Query params:
        months  — optional window size in months. Values > 24 are clamped by the
                  store (not rejected); values < 1 are rejected (400).
        end     — optional "YYYY-MM" window end. Omit to end at the latest
                  populated month.

    Same local-serve posture as /summary. Amounts are str(Decimal), never float.
    """
    if months < 1:
        raise HTTPException(status_code=400, detail="months must be >= 1")
    if end is not None and not re.match(r"^\d{4}-\d{2}$", end):
        raise HTTPException(status_code=400, detail="end must be YYYY-MM")

    return app.state.store.category_trend(months=months, end_month=end)


# ---------------------------------------------------------------------------
# POST /reclassify  (small-fuel-stop dining rule; local-only category edit)
# ---------------------------------------------------------------------------


@app.post("/reclassify")
async def reclassify(
    enabled: Annotated[bool, Query(description="true = apply rule, false = revert")],
    month: Annotated[str | None, Query(description="YYYY-MM")] = None,
):
    """Apply or revert the small-fuel-stop 'Dining Out' rule for a month.

    When enabled=true, Transport rows at fuel/convenience merchants (BP, 7-Eleven,
    etc.) under $10 are moved to 'Dining Out' and marked so the move is reversible.
    When enabled=false, previously-moved rows are restored to 'Transport'.

    This edits the owner's own local SQLite only — no off-machine call. Returns the
    updated summary so the dashboard can re-render.
    """
    if month is not None and not re.match(r"^\d{4}-\d{2}$", month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")

    store = app.state.store
    if enabled:
        store.apply_fuel_dining_rule(month)
    else:
        store.revert_fuel_dining_rule(month)

    return store.summary(month)


# ---------------------------------------------------------------------------
# POST /category-override  (manual category correction + few-shot learning)
# ---------------------------------------------------------------------------

# Valid override targets are exactly the canonical taxonomy labels. 'Uncategorised'
# (a NULL-category view label, not a real category) is intentionally excluded.
_OVERRIDE_CATEGORIES: frozenset[str] = frozenset(TAXONOMY)


@app.post("/category-override")
async def category_override(body: CategoryOverrideIn):
    """Override one transaction's category and remember the correction for few-shot reuse.

    Body (JSON):
        id           — row id (int), OR
        fingerprint  — transaction fingerprint (str); exactly one is required.
        category     — a canonical taxonomy label (400 on anything else).

    Behaviour:
      1. Reject a non-taxonomy category with 400 (junk never touches the store).
      2. Set that transaction's category (local SQLite write only).
      3. Look up the transaction's RAW description, scrub it through the sanitiser,
         and record the (cleaned_description, category) correction so future runs get
         it as a few-shot example. Fail-closed: if the description scrubs to nothing
         safe, the category is still set but NO correction is stored — an
         un-sanitisable string is never persisted or sent off-machine.

    LOCAL-ONLY edit of the owner's own store — same local-serve posture as
    /reclassify. Returns the updated month summary so the dashboard can re-render.
    """
    if body.category not in _OVERRIDE_CATEGORIES:
        raise HTTPException(status_code=400, detail="unknown category")

    if body.id is None and not body.fingerprint:
        raise HTTPException(status_code=400, detail="id or fingerprint required")

    key: int | str = body.id if body.id is not None else body.fingerprint  # type: ignore[assignment]

    store = app.state.store

    # Look up the raw description BEFORE writing so a missing row is a clean 404.
    raw = store.transaction_description(key)
    if raw is None:
        raise HTTPException(status_code=404, detail="transaction not found")

    # Set the category (local write only).
    store.set_categories({key: body.category})

    # Scrub the raw description and record the correction — fail-closed on residue.
    cleaned = scrub_description(raw)
    if not has_residual_identifier(cleaned):
        store.record_correction(cleaned, body.category)

    return store.summary()


# ---------------------------------------------------------------------------
# GET/PUT /category-context  (D1 fixed taxonomy / D2 pre-filled example hints)
# ---------------------------------------------------------------------------


def _category_context_response() -> dict:
    """Serialise the store's 9 canonical categories in the GET/PUT response shape."""
    return {
        "categories": [
            {
                "name": c.name,
                "color": c.color,
                "hints": c.hints,
                "position": c.position,
            }
            for c in app.state.store.get_category_context()
        ]
    }


@app.get("/category-context")
async def get_category_context():
    """Return the 9 canonical categories with stored hints (seeded example hints
    on a fresh DB). Local serve to the owner's own client — same posture as /summary.
    """
    return _category_context_response()


@app.put("/category-context")
async def put_category_context(body: CategoryContextIn):
    """Replace-all of the 9 canonical categories' hints.

    Names not in TAXONOMY are ignored by the store — the fixed 9 categories are
    always what gets written (D1). Returns the freshly-stored list in the same
    shape as GET.
    """
    hints_by_name = {c.name: c.hints for c in body.categories}
    app.state.store.save_category_context(hints_by_name)
    return _category_context_response()


# ---------------------------------------------------------------------------
# POST /push/subscribe, POST /push/unsubscribe  (v2 Pass 3 — inert scaffold)
# ---------------------------------------------------------------------------


@app.post("/push/subscribe")
async def push_subscribe(body: PushSubscriptionIn):
    """Store the caller's OWN device push subscription locally (no off-machine call).

    Malformed bodies are rejected by Pydantic as 422. Returns {"ok": True}.
    """
    app.state.store.upsert_push_subscription(body.model_dump())
    return {"ok": True}


@app.post("/push/unsubscribe")
async def push_unsubscribe(body: PushUnsubscribeIn):
    """Remove a stored subscription by endpoint. No-op safe. Returns {"ok": True, "removed": n}."""
    removed = app.state.store.delete_push_subscription(body.endpoint)
    return {"ok": True, "removed": removed}


# ---------------------------------------------------------------------------
# Entrypoint (used by service/run-backend.ps1 and direct `python backend/app.py`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host=BACKEND_HOST,
        port=BACKEND_PORT,
    )
