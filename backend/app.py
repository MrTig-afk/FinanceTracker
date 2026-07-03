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
GET  /search    Full-text search over the owner's own transactions (LOCAL, read-only;
                ?q=free text, optional ?month=YYYY-MM).
GET  /transfers Internal cross-bank transfer pairs netted out of spending (LOCAL, read-only).
POST /transfers/{pair_id}/untag  Undo one transfer match, restoring each leg's category.
GET  /category-context  The 9 canonical categories with stored hints (D1/D2).
PUT  /category-context  Replace-all of the 9 canonical categories' hints.
POST /category-override Override one transaction's category + remember the correction.
POST /push/subscribe    Store a Web Push subscription locally (v2 Pass 3 scaffold).
POST /push/unsubscribe  Remove a stored Web Push subscription by endpoint.
POST /notify/monthly-reminder  Fire the "new month, upload your statements" push
                (for the always-on scheduler; fail-closed no-op when push is off).
GET  /settings  Owner preferences: corrections_enabled (Feature B gate, default OFF)
                + per-type notification toggles (default ON).
PUT  /settings  Partial update of the above (unknown notification keys ignored).
GET  /budgets   Per-category monthly budgets (budgetable category list + set amounts).
PUT  /budgets   Partial update of budgets ({category: amount|null}; null clears).
GET  /export/transactions.csv  Local CSV download of ALL transactions (owner's data).
POST /reset     Wipe all transaction data (confirm=='RESET'); keep device + prefs.
GET  /categoriser/status  configured bool + uncategorised_count (no network).
POST /categoriser/test    Live minimal OpenRouter probe (no txn data; never raises).
POST /categoriser/retry   Re-run categorisation over NULL-category rows (no re-upload).
GET  /corrections         List stored corrections + Feature B enabled flag.
DELETE /corrections/{cid} Remove one stored correction by id.

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
- GET /search is a LOCAL, read-only full-text lookup over the same store — same
  local-serve posture as /summary. Raw descriptions are the owner's own data,
  served only to the owner's own client; nothing here is ever sent off-machine.
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

import csv
import dataclasses
import io
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.analyser import AnalyserError, OpenRouterClient
from backend.budget_alerts import check_budget_alerts
from backend.data_source import Bank
from backend.drive_uploader import is_configured
from backend.notifier import NOTIFICATION_TYPES, send_monthly_reminder, send_notification
from backend.pipeline import RunReport, UploadedFile, retry_uncategorised, run_pipeline
from backend.store import BUDGET_CATEGORIES, Store, TAXONOMY, TRANSFER_CATEGORY
from backend.subscriptions import check_subscriptions

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
    # One-time backfill so a pre-feature DB gets its internal transfers netted at
    # startup without waiting for the next ingest. Deterministic, idempotent, and
    # LOCAL-ONLY (zero network); no workbook rebuild here (workbooks refresh on the
    # next pipeline run).
    backfill = app.state.store.detect_transfers()
    if backfill.pairs_created > 0:
        # Never let an exclusion be a mystery: count-only notice (fail-closed no-op
        # when push is unconfigured, e.g. a first launch with no subscriptions yet).
        try:
            send_notification(
                app.state.store, "transfer_detected", count=backfill.pairs_created
            )
        except Exception:  # noqa: BLE001 — notifications are best-effort, never fatal
            pass
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
    allow_methods=["GET", "POST", "PUT", "DELETE"],
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
# Pydantic models — settings (Feature E) and reset bodies
# ---------------------------------------------------------------------------


class SettingsIn(BaseModel):
    """Partial settings update. Only provided (non-None) fields are applied.

    corrections_enabled gates Feature B (manual-correction few-shot learning).
    notifications maps a notification type -> enabled; unknown keys are ignored.
    """

    corrections_enabled: bool | None = None
    notifications: dict[str, bool] | None = None


class BudgetsIn(BaseModel):
    """Partial budgets update: {category: amount|null}. null (or "") clears one budget.

    Values may arrive as a JSON string ("600.00"), number, or null. The handler
    validates each entry (Decimal, > 0, <= 10_000_000) before writing anything.
    """

    budgets: dict[str, str | float | int | None]


class ResetIn(BaseModel):
    """Destructive reset confirmation. The handler requires confirm == 'RESET'."""

    confirm: str = Field(min_length=1, max_length=32)


# ---------------------------------------------------------------------------
# POST /upload  (FR-5)
# ---------------------------------------------------------------------------

# How much older than "now" a client-supplied queued_at must be for the upload to
# count as "was queued while the backend was offline" (drives the processed vs
# processed_recovered notification). A live upload sends queued_at ~= now (or none)
# and stays under this threshold; a flushed offline-queue upload sits well above it.
_QUEUED_DELAY_THRESHOLD_SECONDS = 120


def _upload_was_queued(queued_at: str | None) -> bool:
    """True when a client-supplied ISO8601 queued_at is meaningfully in the past.

    Fail-safe: any missing/unparseable value -> False (treat as a live upload).
    Never raises. Naive timestamps are assumed UTC.
    """
    if not queued_at:
        return False
    try:
        parsed = datetime.fromisoformat(queued_at.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - parsed
    return age.total_seconds() >= _QUEUED_DELAY_THRESHOLD_SECONDS


@app.post("/upload")
async def upload(
    commbank: Annotated[UploadFile | None, File()] = None,
    westpac: Annotated[UploadFile | None, File()] = None,
    queued_at: Annotated[str | None, Form()] = None,
):
    """Accept CommBank and/or Westpac CSV files and run the full pipeline.

    Multipart form fields:
        commbank   — optional; CommBank NetBank CSV
        westpac    — optional; Westpac CSV
        queued_at  — optional ISO8601 timestamp the client stamped when it CREATED
                     the upload. If the request only reaches the backend well after
                     that (the client queued it while the backend was offline), the
                     run reports processed_recovered instead of the live processed.

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
        report = run_pipeline(
            uploads,
            store=app.state.store,
            was_queued=_upload_was_queued(queued_at),
        )
    except Exception:
        # Unexpected crash — log it server-side but return a safe generic message.
        logger.exception("run_pipeline raised an unexpected exception")
        # Best-effort catch-all push (no internal detail). Fail-closed + guarded so
        # a notifier problem never masks the original 500.
        try:
            send_notification(app.state.store, "generic_error")
        except Exception:  # noqa: BLE001
            logger.debug("generic_error notification skipped")
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
# GET /search  (v6 — local full-text transaction search)
# ---------------------------------------------------------------------------


@app.get("/search")
async def search(
    q: Annotated[str, Query(description="free-text query")],
    month: Annotated[str | None, Query(description="YYYY-MM")] = None,
):
    """Full-text search over the owner's own transactions (LOCAL, read-only).

    Same local-serve posture as /category-transactions: the owner's data served to
    the owner's own localhost/Tailscale client, never sent off-machine. No OpenRouter,
    no network. Blank q returns the empty shape (not an error). Malformed month -> 400.

    Query params:
        q     — free-text query (whitespace-split, implicit-AND, prefix match).
        month — optional 'YYYY-MM' filter. Omit to search all months.
    """
    if month is not None and not re.match(r"^\d{4}-\d{2}$", month):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")

    return app.state.store.search_transactions(q, month)


# ---------------------------------------------------------------------------
# GET /transfers, POST /transfers/{pair_id}/untag  (v6 — internal transfer netting)
# ---------------------------------------------------------------------------


@app.get("/transfers")
async def transfers():
    """List internal cross-bank transfer pairs the app has netted out of spending.

    LOCAL, read-only view of the owner's own store — same local-serve posture as
    /category-transactions. Descriptions/amounts are the owner's own data served to
    the owner's own localhost/Tailscale client; nothing here is ever sent off-machine.
    No OpenRouter, no network. Empty DB -> {"count": 0, "pairs": []}.
    """
    pairs = app.state.store.list_transfer_pairs()
    return {"count": len(pairs), "pairs": pairs}


@app.post("/transfers/{pair_id}/untag")
async def untag_transfer(pair_id: int):
    """Undo one transfer match ("Not a transfer"), restoring each leg's category.

    LOCAL-ONLY edit of the owner's own store — same local-serve posture as /reclassify.
    Restores each leg's previous category (may be NULL, in which case the row reappears
    as Uncategorised and the retry button can categorise it) and dismisses the pair.

    Unknown id -> 404. Already dismissed -> 200 with restored 0 (idempotent). Success
    -> 200 with restored 2 plus restored_to {out, in} (each a category label or null
    for Uncategorised) so the UI can say where each leg went. FastAPI coerces
    pair_id; a non-int path is an automatic 422.
    """
    result = app.state.store.untag_transfer_pair(pair_id)
    if result is None:
        raise HTTPException(status_code=404, detail="transfer pair not found")
    # A successful untag restores each leg's previous category, which can change a
    # budgeted category's spend — guarded budget-alert check (never raises).
    check_budget_alerts(app.state.store)
    # Restoring a non-Transfer row can also affect recurring-merchant detection — guarded.
    check_subscriptions(app.state.store)
    response: dict = {"ok": True, "pair_id": pair_id, "restored": result["restored"]}
    if result["restored"] > 0:
        response["restored_to"] = {"out": result.get("out"), "in": result.get("in")}
    return response


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
    # Persist the toggle as a preference so it stays where the owner left it,
    # even when there are no eligible (under-$10 fuel) rows to actually move this
    # month. The toggle reflects this preference, not whether any row was moved.
    store.set_bool_setting("fuel_rule_enabled", enabled)
    if enabled:
        store.apply_fuel_dining_rule(month)
    else:
        store.revert_fuel_dining_rule(month)

    # Moving rows between Transport and Dining Out changes those categories' spend —
    # guarded budget-alert check (never raises).
    check_budget_alerts(store)

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
         it as a few-shot example. This recording is GATED by the opt-in
         ``corrections_enabled`` setting (default OFF, Feature E): when off, the
         category override still applies but NO correction is recorded. Fail-closed:
         even when on, if the description scrubs to nothing safe the category is still
         set but NO correction is stored — an un-sanitisable string is never persisted
         or sent off-machine.

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

    # Transfer legs are managed by their pair record: overriding one leg would leave
    # the pair asymmetric, and a later untag would then restore a stale category.
    # Reject with 409 — the owner untags via POST /transfers/{id}/untag first.
    if store.transaction_category(key) == TRANSFER_CATEGORY:
        raise HTTPException(
            status_code=409, detail="transaction is a transfer leg; untag the pair first"
        )

    # Set the category (local write only).
    store.set_categories({key: body.category})

    # Scrub the raw description and record the correction — but ONLY when the owner
    # has opted in (corrections_enabled, default OFF) AND it scrubs to something safe
    # (fail-closed on residue). Both conditions must hold before anything is stored.
    if store.get_bool_setting("corrections_enabled", False):
        cleaned = scrub_description(raw)
        if not has_residual_identifier(cleaned):
            store.record_correction(cleaned, body.category)

    # Re-categorising a row can push a budgeted category over a threshold — guarded
    # budget-alert check (never raises).
    check_budget_alerts(store)
    # An Income re-tag can complete/alter a recurring-deposit streak — guarded.
    check_subscriptions(store)

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
# POST /notify/monthly-reminder  (v4 Feature D — "new month, upload" nudge)
# ---------------------------------------------------------------------------


@app.post("/notify/monthly-reminder")
async def notify_monthly_reminder():
    """Send the monthly "export and upload this month's statements" reminder.

    Intended for the always-on service to hit on a schedule (e.g. Windows Task
    Scheduler on the 1st of each month). Enabling that schedule is a separate OPS
    step; this endpoint just performs one best-effort send when called.

    Fail-closed: with push disabled / placeholder VAPID keys / no subscriptions,
    this is a silent no-op that returns {"ok": True, "sent": 0}. Never raises for a
    delivery problem. Carries NO transaction data (fixed status copy only).
    """
    sent = send_monthly_reminder(app.state.store)
    return {"ok": True, "sent": sent}


# ---------------------------------------------------------------------------
# GET/PUT /settings  (Feature E — owner preferences: corrections + notifications)
# ---------------------------------------------------------------------------


def _settings_response() -> dict:
    """Serialise current owner settings in the GET/PUT /settings response shape.

    corrections_enabled defaults to False (Feature B is opt-in). Each notification
    type defaults to True (opt-out model). LOCAL-ONLY read of the owner's own store.
    """
    store = app.state.store
    return {
        "corrections_enabled": store.get_bool_setting("corrections_enabled", False),
        "notifications": {
            ntype: store.notification_enabled(ntype) for ntype in NOTIFICATION_TYPES
        },
    }


@app.get("/settings")
async def get_settings():
    """Return the owner's preferences (Feature B gate + per-type notification toggles).

    Local serve to the owner's own client — same posture as /summary. No secrets.
    """
    return _settings_response()


@app.put("/settings")
async def put_settings(body: SettingsIn):
    """Apply a PARTIAL settings update; return the full settings in the GET shape.

    Only provided (non-None) fields are written. For ``notifications``, only keys in
    NOTIFICATION_TYPES are accepted (unknown keys are ignored silently). Preferences
    persist in local SQLite only — no off-machine call.
    """
    store = app.state.store
    if body.corrections_enabled is not None:
        store.set_bool_setting("corrections_enabled", body.corrections_enabled)
    if body.notifications is not None:
        for ntype, enabled in body.notifications.items():
            if ntype in NOTIFICATION_TYPES:
                store.set_bool_setting(f"notify:{ntype}", bool(enabled))
    return _settings_response()


# ---------------------------------------------------------------------------
# GET/PUT /budgets  (v6 — per-category monthly budgets + threshold alerts)
# ---------------------------------------------------------------------------

# Upper bound to keep junk out of a monthly dollar budget.
_BUDGET_MAX = Decimal("10000000")


def _budgets_response() -> dict:
    """Serialise the budgetable category list + currently-set budgets.

    ``categories`` is BUDGET_CATEGORIES in canonical order (the server is the source
    of truth for the 7 budgetable names/order). ``budgets`` contains only set entries,
    values as canonical str(Decimal 2dp). LOCAL-ONLY read of the owner's own store.
    """
    budgets = app.state.store.get_budgets()
    return {
        "categories": list(BUDGET_CATEGORIES),
        "budgets": {cat: str(amount) for cat, amount in budgets.items()},
    }


@app.get("/budgets")
async def get_budgets():
    """Return the budgetable categories and any set monthly budgets.

    Local serve to the owner's own client — same posture as /settings. No secrets,
    no network. Amounts are str(Decimal), never float.
    """
    return _budgets_response()


@app.put("/budgets")
async def put_budgets(body: BudgetsIn):
    """Apply a PARTIAL budgets update; return the full budgets in the GET shape.

    For each (category, value): a category not in BUDGET_CATEGORIES is ignored
    silently (mirrors put_settings). A null/empty value clears that budget. Any other
    value must parse as a finite Decimal in (0, 10_000_000]; otherwise the whole
    request is rejected with 400 and NOTHING is written (all entries are validated
    before any write). A successful write triggers a guarded budget-alert check so
    setting a budget below an already-spent total gives immediate feedback. Preferences
    persist in local SQLite only — no off-machine call.
    """
    to_apply: list[tuple[str, Decimal | None]] = []
    for key, value in body.budgets.items():
        if key not in BUDGET_CATEGORIES:
            continue  # unknown category ignored silently
        if value is None or (isinstance(value, str) and value.strip() == ""):
            to_apply.append((key, None))
            continue
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise HTTPException(status_code=400, detail="invalid budget amount")
        if (
            amount.is_nan()
            or amount.is_infinite()
            or amount <= 0
            or amount > _BUDGET_MAX
        ):
            raise HTTPException(status_code=400, detail="invalid budget amount")
        to_apply.append((key, amount))

    store = app.state.store
    for cat, amount in to_apply:
        store.set_budget(cat, amount)

    check_budget_alerts(store)
    return _budgets_response()


# ---------------------------------------------------------------------------
# GET /subscriptions  (v6 — recurring-merchant / income watch, read-only)
# ---------------------------------------------------------------------------


@app.get("/subscriptions")
async def get_subscriptions():
    """Return the detected recurring payments (subscriptions) and regular deposits.

    Read-only LOCAL serve of the owner's own store (same posture as /transfers): no
    store mutation, no detection run, no network. Detection runs only at the guarded
    trigger points (upload / retry / override / transfer untag). `merchant` is the
    stored scrubbed root; `amount` is the expected magnitude as str(Decimal). Rows come
    pre-ordered active-first then ended, each alphabetical. Empty DB -> {"count": 0,
    "subscriptions": []}.
    """
    subs = app.state.store.get_subscriptions()
    return {
        "count": len(subs),
        "subscriptions": [
            {
                "merchant": s["root"],
                "direction": s["direction"],
                "amount": s["expected_amount"],
                "first_seen_month": s["first_seen_month"],
                "last_seen_month": s["last_seen_month"],
                "status": s["status"],
            }
            for s in subs
        ],
    }


# ---------------------------------------------------------------------------
# GET /export/transactions.csv  (Feature E — local CSV download of all rows)
# ---------------------------------------------------------------------------


@app.get("/export/transactions.csv")
async def export_transactions_csv():
    """Download ALL transactions as a CSV file (LOCAL serve of the owner's own data).

    Columns: date, description, amount, category, bank, year_month; ordered by date
    then id. The CSV is built in-memory with the stdlib csv module (RFC-4180 quoting),
    so descriptions containing commas/quotes are escaped correctly. This is the owner's
    own data served to the owner's own client into a download they explicitly
    requested; nothing here is ever sent off-machine. Money values are the stored
    canonical str(Decimal), never float.
    """
    rows = app.state.store.all_transactions_for_export()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "description", "amount", "category", "bank", "year_month"])
    for r in rows:
        writer.writerow(
            [
                r["date"],
                r["description"],
                r["amount"],
                r["category"] if r["category"] is not None else "",
                r["bank"],
                r["year_month"],
            ]
        )

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="financetracker-transactions.csv"'
        },
    )


# ---------------------------------------------------------------------------
# POST /reset  (Feature E — wipe all transaction data; keep device + prefs)
# ---------------------------------------------------------------------------


@app.post("/reset")
async def reset(body: ResetIn):
    """Wipe all transaction data after an explicit confirmation.

    Body (JSON): ``{"confirm": "RESET"}`` — anything else is a 400. On confirmation,
    DELETEs transactions/file_fingerprints/corrections and re-seeds category_context
    to defaults, while PRESERVING push_subscription (device stays subscribed) and
    app_settings (preferences kept). LOCAL-ONLY destructive op; no off-machine call.
    Returns ``{"ok": True, "cleared": {"transactions": n, ...}}``.
    """
    if body.confirm != "RESET":
        raise HTTPException(status_code=400, detail="confirmation required")

    cleared = app.state.store.reset_all_data()
    return {"ok": True, "cleared": cleared}


# ---------------------------------------------------------------------------
# Categoriser health — GET /categoriser/status, POST /categoriser/test|retry
# ---------------------------------------------------------------------------


def _probe_openrouter(client_factory=OpenRouterClient) -> dict:
    """Owner-initiated LIVE connectivity probe for OpenRouter. Never raises.

    Carries NO transaction data — a minimal fixed "ping" call only. The sole
    sanctioned off-machine endpoint. ``client_factory`` is injectable so tests can
    supply a fake client and never touch the network.

    Returns a dict with ``configured``/``reachable``/``rate_limited``/``detail``:
      - key unset:      configured False, no call attempted.
      - success:        reachable True.
      - AnalyserError naming 429 / "rate": rate_limited True (shared free-tier).
      - any other error: reachable False (safe generic detail; never echoes the
        raw exception text or the API key).
    """
    if not os.getenv("OPENROUTER_API_KEY"):
        return {
            "configured": False,
            "reachable": False,
            "rate_limited": False,
            "detail": "OpenRouter API key not configured",
        }

    try:
        client = client_factory()
        client.complete(system_prompt="ping", user_prompt="ping")
    except AnalyserError as exc:
        message = str(exc).lower()
        if "429" in message or "rate" in message:
            return {
                "configured": True,
                "reachable": True,
                "rate_limited": True,
                "detail": "Rate limited (shared free-tier throttling)",
            }
        return {
            "configured": True,
            "reachable": False,
            "rate_limited": False,
            "detail": "Could not reach OpenRouter",
        }
    except Exception:  # noqa: BLE001 — never leak internal detail; safe generic string
        return {
            "configured": True,
            "reachable": False,
            "rate_limited": False,
            "detail": "Could not reach OpenRouter",
        }

    return {
        "configured": True,
        "reachable": True,
        "rate_limited": False,
        "detail": "OpenRouter reachable",
    }


@app.get("/categoriser/status")
async def categoriser_status():
    """Report categoriser configuration + pending workload. NO network call.

    ``configured`` is a boolean only (never the key value). ``uncategorised_count``
    is how many rows still need a category (drives the app's "retry" affordance).
    """
    return {
        "configured": bool(os.getenv("OPENROUTER_API_KEY")),
        "uncategorised_count": len(app.state.store.uncategorised()),
    }


@app.post("/categoriser/test")
async def categoriser_test():
    """Owner-initiated LIVE OpenRouter connectivity probe (see _probe_openrouter).

    Carries NO transaction data (a fixed "ping" only). Never raises; returns a safe
    status dict. When the key is unset, no call is attempted.
    """
    return _probe_openrouter()


@app.post("/categoriser/retry")
async def categoriser_retry():
    """Re-run categorisation over NULL-category rows WITHOUT a re-upload.

    Ties into the pipeline's orphan-recovery path (retry_uncategorised): reuses the
    exact sanitise -> gated few-shot preamble -> categorise -> set_categories ->
    rebuild-workbooks path. Never raises: on OpenRouter being down it returns a safe
    ``{"ok": False, ..., "detail": "categoriser unavailable"}`` with HTTP 200.
    """
    return retry_uncategorised(app.state.store)


# ---------------------------------------------------------------------------
# Learned corrections — GET /corrections, DELETE /corrections/{cid}  (Feature E)
# ---------------------------------------------------------------------------


@app.get("/corrections")
async def get_corrections():
    """List the owner's stored category corrections + the Feature B enabled flag.

    LOCAL serve of the owner's own store. Only the already-scrubbed
    cleaned_description is stored/returned here, never a raw bank description.
    """
    store = app.state.store
    return {
        "enabled": store.get_bool_setting("corrections_enabled", False),
        "corrections": store.list_corrections(),
    }


@app.delete("/corrections/{cid}")
async def delete_correction(cid: int):
    """Delete one stored correction by id. Idempotent no-op safe.

    Returns ``{"ok": True, "removed": n}`` (n is 0 when the id was not present).
    """
    removed = app.state.store.delete_correction(cid)
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
