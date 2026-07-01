"""pipeline.py — end-to-end orchestration for FinanceTracker (§7.2, FR-5..FR-7).

Composes the already-built stages in the exact order defined by the spec:
  Layer 1  — per-file fingerprint tracking. Every uploaded file is decoded and
             parsed (so balance reconciliation always sees its rows); an
             already-processed file is counted as "skipped" and is NOT
             re-fingerprinted, but its parsed rows still flow into balance
             reconciliation and Layer 2's dedupe.
  Layer 2  — per-transaction fingerprint dedupe (skip rows already in the store)
  Layer 3  — categorise only-new rows via the sanitiser + analyser
  Output   — one monthly Excel workbook per distinct month; optional Drive upload

Privacy contract
----------------
- Raw CSV bytes (uf.content) are NEVER written to a tracked path; they exist in memory
  only for the duration of this call.
- The only off-machine call is inside categorise() (the analyser), which receives ONLY
  the sanitised SanitiseResult.payload — (row_index, cleaned_description, amount) tuples.
- Error messages in RunReport.errors are fixed safe strings; they never contain raw
  descriptions, amounts, account numbers, or exception str() output.

Injectable seams
----------------
All three external dependencies — SQLite store, analyser client, Drive service — are
parameters.  Tests pass ":memory:" stores, fake clients (zero network), and no Drive
service.  Production callers pass no overrides and rely on env-configured defaults.

No IO, network, or file creation occurs on a bare ``import backend.pipeline``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.data_source import Bank, Transaction, detect_bank, parse_text
from backend.idempotency import (
    file_fingerprint,
    filter_new_transactions,
    is_noop,
    select_uncategorised,
    transaction_fingerprint,
)
from backend.sanitiser import sanitise
from backend.analyser import categorise, build_context_prompt
from backend.store import Store
from backend.excel_builder import build_workbook
from backend.drive_uploader import upload_file

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain dataclasses (frozen — immutable after construction)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UploadedFile:
    """One bank CSV file received in the upload request.

    Carries raw bytes in memory only — content is NEVER written to a tracked path.
    filename is for display purposes only; it is never echoed verbatim in error messages
    that could be seen by a third party (all errors are fixed safe strings).
    """

    filename: str   # display name; e.g. file.filename or "<bank>.csv"
    bank: Bank      # which parser profile to use
    content: bytes  # raw CSV bytes — in memory only, never persisted to a tracked path


@dataclass(frozen=True)
class RunReport:
    """Summary of one pipeline run, safe to serialise and return to the caller.

    All string fields are either counts, model identifiers, path strings (for
    locally-written files), Drive IDs, or fixed safe error messages.
    No raw transaction text, amounts, account numbers, or secret values appear here.
    """

    files_seen: int           # total UploadedFile objects received
    files_skipped: int        # Layer-1 file-fingerprint hits (already processed)
    new_txns: int             # genuinely-new transactions from Layer-2 filter
    categorised: int          # rows that received a category this run (Layer 3)
    model_used: str           # AnalysisResult.model_used ("" when no LLM call)
    excel_path: str | None    # str(Path) of the last written workbook, or None
    drive_file_id: str | None # Drive file id, or None (unconfigured / no new data)
    noop: bool                # True when nothing new AND no balance changed (FR-15, Q3)
    year_month: str | None    # "YYYY-MM" of the last month produced, or None on no-op
    errors: list[str]         # safe human-readable messages; never raw txn/account text
    balance_updates: int      # rows whose balance was corrected in place this run (Q3)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_pipeline(
    uploads: list[UploadedFile],
    *,
    store: Store,
    analyser_client=None,       # OpenRouterClient | fake; None → lazy-construct real one
    drive_service=None,         # injected Drive service; None → env-gated Drive
    sanitise_log_dir=None,      # forwarded to sanitise(log_dir=...); tests pass tmp_path
    output_dir=None,            # forwarded to build_workbook(output_dir=...); tests pass tmp_path
) -> RunReport:
    """Run all pipeline stages for a batch of uploaded files.

    Composes the already-built stages.  Does NOT reimplement any stage.

    Parameters
    ----------
    uploads:
        One entry per bank file received.  May be empty → treated as no-op.
    store:
        Open Store instance managed by the caller (app lifespan or test fixture).
    analyser_client:
        OpenRouterClient (or any object with a matching .complete() method).
        When None, categorise() constructs a real client from env vars only when
        actually needed (empty payload short-circuits before construction).
    drive_service:
        Injected Drive service for tests.  None → config-gated real upload.
    sanitise_log_dir:
        Override audit log directory.  Tests pass tmp_path; production uses $LOG_DIR.
    output_dir:
        Override workbook output directory.  Tests pass tmp_path; production uses $OUTPUT_DIR.

    Returns
    -------
    RunReport
        Always returned (never raises); unexpected errors are caught and recorded as
        safe messages in RunReport.errors.
    """
    errors: list[str] = []
    skipped = 0
    all_parsed_txns: list[Transaction] = []

    # ------------------------------------------------------------------
    # Layer 1 — per-file fingerprint tracking (FR-12)
    # ------------------------------------------------------------------
    for uf in uploads:
        fp = file_fingerprint(uf.content)

        # An already-processed file is still decoded and parsed below — its rows
        # feed reconcile_balances() so a re-uploaded (byte-identical or
        # balance-corrected) file can backfill/correct stored balances. It is
        # NOT re-fingerprinted (see the `if not already_processed` guard below),
        # and Layer 2's transaction-fingerprint dedupe guarantees its rows are
        # never re-inserted or re-sent to the analyser.
        already_processed = store.is_file_processed(fp)
        if already_processed:
            skipped += 1
            logger.debug("file already processed (re-parsed for balance reconciliation): %s", uf.filename)

        # Decode: prefer UTF-8 with BOM stripping; fall back to replace-mode on error.
        try:
            text = uf.content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = uf.content.decode("utf-8", errors="replace")

        # Choose the parser by the file's ACTUAL contents, not the upload box it
        # arrived in. A file that matches neither profile is rejected here and,
        # crucially, NOT fingerprinted — so re-uploading a corrected file works.
        detected: Bank | None = detect_bank(text)
        if detected is None:
            errors.append(f"unrecognised CSV format: {uf.filename}")
            continue

        # All parse work is wrapped so one bad file never aborts the batch.
        try:
            txns = parse_text(text, detected)
        except Exception:
            # Never include exception str — it could contain raw CSV values.
            errors.append(f"failed to parse {uf.filename} ({detected.value})")
            continue

        all_parsed_txns.extend(txns)
        # Mark processed AFTER a successful parse, and only for genuinely-new
        # files — an already-processed file keeps its original fingerprint
        # record (re-marking it would be a harmless no-op anyway, but this
        # keeps the intent explicit).
        if not already_processed:
            store.mark_file_processed(fp)
        logger.debug(
            "file parsed: %s — detected %s — %d rows",
            uf.filename, detected.value, len(txns),
        )

    # Q3: correct balances on rows ALREADY in the store — this now covers BOTH a
    # byte-different re-upload carrying a corrected balance AND a byte-identical
    # re-upload of a file whose rows were first stored under an older,
    # balance-less schema (all_parsed_txns includes already-processed files'
    # rows too — see Layer 1 above). Local-only SQLite work, zero network, and
    # it never touches category — see Store.reconcile_balances.
    balance_updates = store.reconcile_balances(all_parsed_txns)

    # ------------------------------------------------------------------
    # Layer 2 — transaction-level dedupe (FR-13 / FR-15)
    # ------------------------------------------------------------------
    result = filter_new_transactions(all_parsed_txns, store.seen_transaction_fingerprints())

    if is_noop(result) and balance_updates == 0:
        # Nothing new AND no balance changed — no sanitise, no LLM, no store write,
        # no Excel, no Drive (FR-15). A truly-identical re-run (same bytes, and
        # every parsed balance already matches what's stored) reaches here too:
        # Layer 1 parses the file again, but reconcile_balances finds nothing to
        # change (balance_updates stays 0) and Layer 2 finds no new fingerprints.
        # A re-upload whose balances differ from what's stored (e.g. the file was
        # first ingested before this feature existed, so stored balances are
        # NULL) does NOT reach here — balance_updates > 0 takes the fall-through
        # path below, which persists the correction with zero analyser calls.
        logger.info(
            "pipeline no-op: files_seen=%d files_skipped=%d",
            len(uploads),
            skipped,
        )
        return RunReport(
            files_seen=len(uploads),
            files_skipped=skipped,
            new_txns=0,
            categorised=0,
            model_used="",
            excel_path=None,
            drive_file_id=None,
            noop=True,
            year_month=None,
            errors=errors,
            balance_updates=0,
        )

    # Persist the new rows (upsert — balance-only on conflict; double-run safe).
    store.add_new(result)

    # ------------------------------------------------------------------
    # Layer 3 — categorise only-new (FR-14)
    # ------------------------------------------------------------------
    to_categorise: list[Transaction] = select_uncategorised(
        list(result.new_transactions),
        store.categorised_fingerprints(),
    )

    # Sanitise before any off-machine call — fail-closed (FR-16..FR-21).
    sresult = sanitise(to_categorise, audit=True, log_dir=sanitise_log_dir)

    # Build the "TAXONOMY & CONTEXT" preamble from the owner's stored category
    # hints (local-only data — never transaction text) and prepend it to the
    # system prompt. The pipeline owns the Store, so it builds this here to keep
    # the analyser decoupled from Store (it only ever receives strings).
    preamble = build_context_prompt(store.get_category_context())

    # categorise() short-circuits with zero HTTP calls when sresult.payload is empty.
    analysis = categorise(sresult, client=analyser_client, context_preamble=preamble)

    # Map row_index (position in to_categorise) back to fingerprint so set_categories
    # can write by fingerprint key.  sanitise() assigns row_index = enumerate position.
    mapping = {
        transaction_fingerprint(to_categorise[ri]): cat
        for ri, cat in analysis.categories.items()
        if ri < len(to_categorise)  # defensive: ignore out-of-range indexes
    }
    categorised = store.set_categories(mapping)
    model_used = analysis.model_used

    logger.info(
        "pipeline categorised %d rows via model %r",
        categorised,
        model_used or "(none)",
    )

    # ------------------------------------------------------------------
    # Output — Excel workbook + optional Drive upload (FR-30, FR-31)
    # Only runs when there is genuinely-new data this run.
    # ------------------------------------------------------------------
    excel_path: str | None = None
    drive_file_id: str | None = None
    year_month: str | None = None

    # Build one workbook per distinct month present in the new transactions.
    # Multi-month uploads (overlapping exports) produce one file per month.
    months = sorted({t.date.isoformat()[:7] for t in result.new_transactions})

    for ym in months:
        try:
            path = build_workbook(
                ym,
                store.transactions_for_month(ym),
                store.summary(ym),
                output_dir=output_dir,
            )
            yr, mo = ym.split("-")
            fid = upload_file(path, year=yr, month=mo, service=drive_service)
            # Record the last successfully-written month (most recent after sort).
            excel_path = str(path)
            drive_file_id = fid
            year_month = ym
        except Exception:
            # Never include exception str — it could contain file paths or raw data.
            errors.append(f"excel/drive step failed for {ym}")

    return RunReport(
        files_seen=len(uploads),
        files_skipped=skipped,
        new_txns=len(result.new_transactions),
        categorised=categorised,
        model_used=model_used,
        excel_path=excel_path,
        drive_file_id=drive_file_id,
        noop=False,
        year_month=year_month,
        errors=errors,
        balance_updates=balance_updates,
    )
