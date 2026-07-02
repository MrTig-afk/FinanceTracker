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
  Notify   — best-effort Web Push from the catalog (processed / categorisation_failed
             / parse_error / drive_backup_failed / duplicate_noop / *_recovered).
             Fail-closed no-op unless PUSH_ENABLED + real VAPID keys + a subscription
             exist; a notifier failure NEVER breaks the run. Copy is counts/status
             only (see backend.notifier) — never amounts, merchants, or names.

Privacy contract
----------------
- Raw CSV bytes (uf.content) are NEVER written to a tracked path; they exist in memory
  only for the duration of this call.
- The only off-machine call is inside categorise() (the analyser), which receives ONLY
  the sanitised SanitiseResult.payload — (row_index, cleaned_description, amount) tuples.
- Error messages in RunReport.errors are fixed safe strings; they never contain raw
  descriptions, amounts, account numbers, or exception str() output.
- The push-notification step (see Notify above) is feature-flagged OFF by default and
  never sends transaction data even when active (counts/status only) — see backend/notifier.

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

from backend.data_source import (
    Bank,
    Transaction,
    detect_bank,
    parse_text,
    upload_to_csv_text,
)
from backend.idempotency import (
    file_fingerprint,
    filter_new_transactions,
    is_noop,
)
from backend.sanitiser import sanitise
from backend.analyser import categorise, build_context_prompt, AnalyserError
from backend.store import Store
from backend.store.splitwise_rule import match_splitwise_tag
from backend.excel_builder import build_workbook
from backend.drive_uploader import upload_file
from backend.notifier import send_notification

logger = logging.getLogger(__name__)

# Human-readable bank names for notification copy (status only — never data).
_BANK_DISPLAY = {
    Bank.COMMBANK: "CommBank",
    Bank.WESTPAC: "Westpac",
}


def _notify(store, ntype: str, *, count: int | None = None, detail: str | None = None) -> None:
    """Fire one catalog notification, fully guarded.

    A notifier failure (bad endpoint, missing dep, anything) must NEVER break a
    pipeline run, mirroring the original send_processed_notification call site.
    The notifier itself is fail-closed: a hard no-op unless push is enabled with
    real VAPID keys and at least one subscription exists.
    """
    try:
        send_notification(store, ntype, count=count, detail=detail)
    except Exception:  # noqa: BLE001 — notifications are best-effort, never fatal
        logger.debug("push notification step skipped (%s)", ntype)

# ---------------------------------------------------------------------------
# Domain dataclasses (frozen — immutable after construction)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UploadedFile:
    """One bank statement file (CSV or .xlsx) received in the upload request.

    Carries raw bytes in memory only — content is NEVER written to a tracked path.
    filename is for display purposes only; it is never echoed verbatim in error messages
    that could be seen by a third party (all errors are fixed safe strings). It is also
    a hint for xlsx routing (a ``.xlsx`` extension), though the ZIP magic bytes are the
    authoritative signal.
    """

    filename: str   # display name / routing hint; e.g. file.filename or "<bank>.csv"
    bank: Bank      # which upload box it arrived in (parser is chosen by content)
    content: bytes  # raw CSV or .xlsx bytes — in memory only, never persisted to a tracked path


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
    was_queued: bool = False,   # True when this upload was queued while the backend was offline
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
    was_queued:
        Recovered-vs-live signal for the success notification. When True, a
        successful run reports ``processed_recovered`` ("Backend back online -
        your queued upload is now processed") instead of the live ``processed``.
        The /upload endpoint derives this from a client-supplied ``queued_at``
        timestamp: if the upload was created meaningfully before it reached the
        backend (i.e. it sat in the client's offline queue), was_queued is True.
        This is distinct from ``categorisation_recovered`` (OpenRouter was down,
        orphan rows are now sorted), which is derived from store state below.

    Notifications (best-effort, fail-closed, never fatal)
    -----------------------------------------------------
    Exactly one terminal notification per run, chosen by severity priority:
      categorisation_failed  > drive_backup_failed > parse_error > success.
    The success notification is processed_recovered (was_queued), else
    categorisation_recovered (prior orphan rows cleared this run), else processed.
    A true no-op fires duplicate_noop (or parse_error if the only files failed to
    parse). generic_error is the app-level catch-all for an unexpected crash.

    Returns
    -------
    RunReport
        Always returned (never raises); unexpected errors are caught and recorded as
        safe messages in RunReport.errors.
    """
    errors: list[str] = []
    skipped = 0
    all_parsed_txns: list[Transaction] = []
    # First bank whose file failed to parse / was unrecognised (for parse_error copy).
    parse_error_bank: str | None = None

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

        # Normalise the upload to CSV text. A CSV is decoded (UTF-8 w/ BOM,
        # tolerant fallback); an .xlsx (detected by ZIP magic bytes or filename)
        # is read by openpyxl and flattened to CSV text so it flows through the
        # exact same detection + per-bank parsers below. A corrupt/unreadable
        # xlsx raises ValueError and is treated like an unrecognised file — the
        # batch is not aborted and the file is NOT fingerprinted.
        try:
            text = upload_to_csv_text(uf.content, uf.filename)
        except ValueError:
            errors.append(f"unrecognised file format: {uf.filename}")
            if parse_error_bank is None:
                parse_error_bank = _BANK_DISPLAY.get(uf.bank, uf.bank.value)
            continue

        # Choose the parser by the file's ACTUAL contents, not the upload box it
        # arrived in. A file that matches neither profile is rejected here and,
        # crucially, NOT fingerprinted — so re-uploading a corrected file works.
        detected: Bank | None = detect_bank(text)
        if detected is None:
            errors.append(f"unrecognised CSV format: {uf.filename}")
            if parse_error_bank is None:
                parse_error_bank = _BANK_DISPLAY.get(uf.bank, uf.bank.value)
            continue

        # All parse work is wrapped so one bad file never aborts the batch.
        try:
            txns = parse_text(text, detected)
        except Exception:
            # Never include exception str — it could contain raw CSV values.
            errors.append(f"failed to parse {uf.filename} ({detected.value})")
            if parse_error_bank is None:
                parse_error_bank = _BANK_DISPLAY.get(detected, detected.value)
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

    # Rows already in the store that never received a category — e.g. a prior run
    # persisted them (add_new below) but the analyser call then failed, or was
    # rate-limited. They are "seen", so Layer 2 excludes them from
    # result.new_transactions; without this recovery they stay NULL forever
    # because a re-upload never re-enters them into categorisation. Fetched before
    # add_new so the no-op guard can account for them.
    pending_uncategorised = store.uncategorised()
    # Prior orphans present at the START of this run (before add_new). If this run
    # then categorises successfully, these were rescued from an earlier failed /
    # rate-limited categorisation -> categorisation_recovered signal below.
    prior_orphans = len(pending_uncategorised)

    if is_noop(result) and balance_updates == 0 and not pending_uncategorised:
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
        # Nothing new was ingested. If that is only because every file failed to
        # parse, surface the parse failure; otherwise it is a genuine duplicate.
        if parse_error_bank is not None:
            _notify(store, "parse_error", detail=parse_error_bank)
        else:
            _notify(store, "duplicate_noop")
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
    # Categorise EVERY currently-uncategorised row read straight from the store:
    # the rows just inserted by add_new PLUS any recovered orphans from earlier
    # failed runs. Reading from the store (not just result.new_transactions) is
    # what closes the NULL-category orphan hole. UncategorisedRow carries .id,
    # .description and .amount — all sanitise() needs.
    to_categorise = store.uncategorised()

    # Deterministic Splitwise self-tag pass — BEFORE sanitise(), on the RAW local
    # description. The owner writes their own tag (e.g. "Splitwise utilities") into
    # PayID/Osko references; the sanitiser would eat those words or fail-closed-drop
    # the whole P2P row, so the LLM can never see the tag. We categorise these rows
    # here and EXCLUDE them from the LLM payload (privacy: the reference may carry a
    # friend's name, and it never leaves the machine).
    splitwise_mapping: dict[int, str] = {}
    remaining: list = []
    for row in to_categorise:
        label = match_splitwise_tag(row.description)
        if label is not None:
            splitwise_mapping[row.id] = label
        else:
            remaining.append(row)

    tagged_rows = [r for r in to_categorise if r.id in splitwise_mapping]
    splitwise_count = (
        store.set_categories(splitwise_mapping) if splitwise_mapping else 0
    )

    # Rebind so the list handed to sanitise() and re-indexed in the mapping below is
    # the SAME shrunken list — preserves the row_index = enumerate-position invariant.
    to_categorise = remaining

    # Sanitise before any off-machine call — fail-closed (FR-16..FR-21).
    sresult = sanitise(to_categorise, audit=True, log_dir=sanitise_log_dir)

    # Build the "TAXONOMY & CONTEXT" preamble from the owner's stored category
    # hints (local-only data — never transaction text) and prepend it to the
    # system prompt. The pipeline owns the Store, so it builds this here to keep
    # the analyser decoupled from Store (it only ever receives strings).
    #
    # Few-shot learning: recent manual corrections (already sanitiser-scrubbed
    # cleaned_description + category — never a raw description) are appended as
    # "Examples of how the owner has corrected categories:" so future
    # categorisation follows the owner's past overrides. user_prompt is unchanged.
    preamble = build_context_prompt(
        store.get_category_context(),
        store.recent_corrections(),
    )

    # categorise() short-circuits with zero HTTP calls when sresult.payload is empty.
    # AnalyserError (all model tiers failed / OpenRouter down) is caught here rather
    # than propagated: the rows already persisted by add_new stay uncategorised
    # (NULL category) as orphans and are recovered on a later run (see the
    # pending_uncategorised recovery above). The run still returns a RunReport and
    # fires the categorisation_failed notification below.
    categorisation_failed = False
    try:
        analysis = categorise(sresult, client=analyser_client, context_preamble=preamble)
    except AnalyserError:
        categorisation_failed = True
        errors.append("categorisation failed - transactions saved as pending")
        analysis = None

    if analysis is not None:
        # Map row_index (position in to_categorise) back to the row's primary-key id
        # so set_categories writes by id.  sanitise() assigns row_index = enumerate
        # position, so to_categorise[ri] is the row that produced category `cat`.
        mapping = {
            to_categorise[ri].id: cat
            for ri, cat in analysis.categories.items()
            if ri < len(to_categorise)  # defensive: ignore out-of-range indexes
        }
        categorised = store.set_categories(mapping) + splitwise_count
        model_used = analysis.model_used
    else:
        # Only the deterministic Splitwise rows (if any) were categorised this run.
        categorised = splitwise_count
        model_used = ""

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
    # First month whose Excel/Drive step failed (for the drive_backup_failed copy).
    drive_failed_month: str | None = None

    # Skip the workbook/backup step entirely when categorisation wholly failed —
    # the rows are still uncategorised orphans, so a workbook would be incomplete
    # and a Drive upload would overwrite last good backup with partial data. The
    # orphan-recovery run that later categorises them rebuilds the month then.
    months: list[str] = []
    if not categorisation_failed:
        # Build one workbook per distinct month touched this run: months present in
        # the new transactions PLUS months of any recovered orphan rows (so a run
        # that only fixed previously-NULL categories still refreshes their workbook).
        # Multi-month uploads (overlapping exports) produce one file per month.
        months = sorted(
            {t.date.isoformat()[:7] for t in result.new_transactions}
            | {row.date[:7] for row in to_categorise}
            | {row.date[:7] for row in tagged_rows}
        )

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
            if drive_failed_month is None:
                drive_failed_month = ym

    # ------------------------------------------------------------------
    # Notify — exactly one terminal notification, by severity priority.
    # Best-effort + fail-closed (see _notify / backend.notifier): a hard no-op
    # unless push is enabled with real VAPID keys AND a subscription exists, and
    # a notifier failure never breaks the run. Copy is counts/status only.
    # ------------------------------------------------------------------
    remaining_uncat = len(store.uncategorised())
    if categorisation_failed or remaining_uncat > 0:
        # OpenRouter down (or some rows left NULL) -> rows saved as pending.
        _notify(store, "categorisation_failed", count=remaining_uncat)
    elif drive_failed_month is not None:
        _notify(store, "drive_backup_failed", detail=drive_failed_month)
    elif parse_error_bank is not None:
        # Some rows processed fine, but at least one file could not be read.
        _notify(store, "parse_error", detail=parse_error_bank)
    elif was_queued:
        # Upload had been queued while the backend was offline; now flushed.
        _notify(store, "processed_recovered", count=categorised)
    elif prior_orphans > 0:
        # Earlier categorisation failure(s) now cleared this run.
        _notify(store, "categorisation_recovered", count=prior_orphans)
    else:
        _notify(store, "processed", count=categorised)

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
