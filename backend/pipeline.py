"""pipeline.py — end-to-end orchestration for FinanceTracker (§7.2, FR-5..FR-7).

Composes the already-built stages in the exact order defined by the spec:
  Layer 1  — per-file fingerprint tracking. Every uploaded file is decoded and
             parsed (so balance reconciliation always sees its rows); an
             already-processed file is counted as "skipped" and is NOT
             re-fingerprinted, but its parsed rows still flow into balance
             reconciliation and Layer 2's dedupe.
  Layer 2  — per-transaction fingerprint dedupe (skip rows already in the store)
  Layer 2.5 — deterministic transfer netting, local only, no LLM: match cross-bank
             internal transfers and tag both legs 'Transfer' so they drop out of the
             LLM payload and every spending aggregate.
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
from backend.budget_alerts import check_budget_alerts
from backend.subscriptions import check_subscriptions

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
    transfer_pairs: int       # internal cross-bank transfer pairs netted this run (0 on no-op)


@dataclass(frozen=True)
class _CategoriseOutcome:
    """Result of one categorise-pending pass (Layer 3 + Output), shared by
    run_pipeline and retry_uncategorised. All string fields are safe (counts,
    model ids, local paths, Drive ids, "YYYY-MM") — never raw transaction text.
    """

    categorised: int              # rows categorised this pass (incl. Splitwise rows)
    model_used: str               # tier that answered ("" when no LLM call / failed)
    categorisation_failed: bool   # True when the analyser exhausted all tiers
    excel_path: str | None        # str(Path) of the last written workbook, or None
    drive_file_id: str | None     # Drive file id, or None (unconfigured / no rebuild)
    year_month: str | None        # "YYYY-MM" of the last month produced, or None
    drive_failed_month: str | None  # first month whose excel/Drive step failed, or None


# ---------------------------------------------------------------------------
# Layer 3 (categorise) + Output — shared by run_pipeline and retry_uncategorised
# ---------------------------------------------------------------------------


def _categorise_pending(
    store,
    *,
    analyser_client=None,
    drive_service=None,
    sanitise_log_dir=None,
    output_dir=None,
    errors: list[str],
    extra_months: tuple[str, ...] = (),
) -> _CategoriseOutcome:
    """Categorise EVERY currently-uncategorised row, then rebuild affected months.

    This is the single implementation of Layer 3 (FR-14) + Output (FR-30, FR-31),
    reused by both run_pipeline (right after add_new) and retry_uncategorised (orphan
    recovery without a fresh upload) so there is no divergent categorisation logic.

    Steps (identical to the original inline pipeline block):
      1. Read store.uncategorised() — rows just inserted PLUS earlier orphans.
      2. Deterministic Splitwise self-tag pass on the RAW local description, BEFORE
         sanitise(): the owner's own tag (e.g. "Splitwise utilities") lives in a
         PayID/Osko reference the sanitiser would eat / fail-closed-drop, so the LLM
         can never see it. These rows are categorised locally and EXCLUDED from the
         off-machine payload (privacy: the reference may carry a friend's name).
      3. Sanitise the remaining rows — fail-closed (FR-16..FR-21).
      4. Build the "TAXONOMY & CONTEXT" preamble from the owner's stored category
         hints (local-only, never transaction text). Few-shot learning is GATED by
         the ``corrections_enabled`` app setting (default OFF): when off, ZERO
         examples are injected and the preamble is byte-identical to the
         no-corrections form; when on, recent (already sanitiser-scrubbed)
         corrections are appended. user_prompt is never affected either way.
      5. categorise() — short-circuits with ZERO HTTP calls on an empty payload;
         AnalyserError (all tiers down) is caught, not propagated, so the persisted
         rows stay NULL-category orphans for a later recovery run.
      6. Rebuild one workbook per distinct month touched (skipped entirely when
         categorisation wholly failed, to avoid a partial backup).

    The only off-machine call is inside categorise() (the analyser), which receives
    ONLY the sanitised (row_index, cleaned_description, amount) tuples. Safe error
    strings are appended to ``errors``; raw data never appears there.
    """
    to_categorise = store.uncategorised()

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

    sresult = sanitise(to_categorise, audit=True, log_dir=sanitise_log_dir)

    # Few-shot learning is opt-in (corrections_enabled, default OFF). When off, an
    # empty tuple is injected so the preamble is byte-identical to the
    # no-corrections form.
    corrections = (
        store.recent_corrections()
        if store.get_bool_setting("corrections_enabled", False)
        else ()
    )
    preamble = build_context_prompt(store.get_category_context(), corrections)

    categorisation_failed = False
    try:
        analysis = categorise(sresult, client=analyser_client, context_preamble=preamble)
    except AnalyserError:
        categorisation_failed = True
        errors.append("categorisation failed - transactions saved as pending")
        analysis = None

    if analysis is not None:
        # Map row_index (position in to_categorise) back to the row's primary-key id.
        mapping = {
            to_categorise[ri].id: cat
            for ri, cat in analysis.categories.items()
            if ri < len(to_categorise)  # defensive: ignore out-of-range indexes
        }
        categorised = store.set_categories(mapping) + splitwise_count
        model_used = analysis.model_used
    else:
        # Only the deterministic Splitwise rows (if any) were categorised this pass.
        categorised = splitwise_count
        model_used = ""

    # Output — one workbook per distinct month touched (skipped when categorisation
    # wholly failed: the rows are still orphans, so a workbook would be incomplete
    # and a Drive upload would overwrite the last good backup with partial data).
    excel_path: str | None = None
    drive_file_id: str | None = None
    year_month: str | None = None
    drive_failed_month: str | None = None

    # extra_months carries months whose only new rows this run were internal transfers
    # (tagged locally by detect_transfers, so they never appear in to_categorise). They
    # still need their workbook rebuilt — but only when categorisation did not wholly
    # fail, preserving the no-partial-backup rule.
    months: list[str] = []
    if not categorisation_failed:
        months = sorted(
            {row.date[:7] for row in to_categorise}
            | {row.date[:7] for row in tagged_rows}
            | set(extra_months)
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
            excel_path = str(path)
            drive_file_id = fid
            year_month = ym
        except Exception:
            # Never include exception str — it could contain file paths or raw data.
            errors.append(f"excel/drive step failed for {ym}")
            if drive_failed_month is None:
                drive_failed_month = ym

    return _CategoriseOutcome(
        categorised=categorised,
        model_used=model_used,
        categorisation_failed=categorisation_failed,
        excel_path=excel_path,
        drive_file_id=drive_file_id,
        year_month=year_month,
        drive_failed_month=drive_failed_month,
    )


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

    affected_balance_months = sorted({t.date.isoformat()[:7] for t in all_parsed_txns})

    # v7: opportunistic backfill snapshot, BEFORE the no-op guard on purpose
    # (DECISION): a re-upload of an already-processed file still parses (see
    # Layer 1 above), so re-uploading an old statement is the ONLY way a
    # pre-feature month (one whose balances row was never written, e.g. an older
    # DB) can enter the history on a run that otherwise short-circuits at the
    # no-op guard below. This call only sees rows ALREADY persisted by a PRIOR
    # run — on a first-time upload the new rows are not in the transactions
    # table yet, so it derives nothing here; the second call below (after
    # add_new) is what covers that case. record_month_balances is an idempotent
    # upsert-on-change, so calling it twice per run is safe: unchanged closings
    # write nothing. LOCAL-ONLY SQLite work; zero network.
    store.record_month_balances(affected_balance_months)

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
            transfer_pairs=0,
        )

    # Persist the new rows (upsert — balance-only on conflict; double-run safe).
    store.add_new(result)

    # v7: snapshot again now that add_new has persisted this run's new rows —
    # this is what populates the balances history on a FIRST-TIME upload (the
    # early call above only sees rows a PRIOR run already stored). Same
    # idempotent upsert-on-change semantics: a re-upload whose rows were already
    # captured by the early call above writes nothing here. LOCAL-ONLY SQLite
    # work; zero network.
    store.record_month_balances(affected_balance_months)

    # ------------------------------------------------------------------
    # Layer 2.5 — deterministic internal-transfer netting (LOCAL, no LLM).
    # ------------------------------------------------------------------
    # Runs AFTER add_new (so both legs are stored) and BEFORE _categorise_pending
    # (so a matched pair's non-NULL 'Transfer' category keeps it OUT of the
    # uncategorised() set and therefore out of the sanitised off-machine payload).
    # Idempotent and zero-network.
    transfer_result = store.detect_transfers()

    # ------------------------------------------------------------------
    # Layer 3 (categorise only-new, FR-14) + Output (Excel + optional Drive).
    # ------------------------------------------------------------------
    # Delegated to _categorise_pending() so the orphan-recovery endpoint
    # (retry_uncategorised) runs the byte-identical sanitise -> preamble ->
    # categorise -> set_categories -> rebuild-workbooks path without a fresh
    # upload. It categorises EVERY currently-uncategorised row (the rows just
    # inserted by add_new PLUS any recovered orphans from earlier failed runs),
    # which is what closes the NULL-category orphan hole.
    outcome = _categorise_pending(
        store,
        analyser_client=analyser_client,
        drive_service=drive_service,
        sanitise_log_dir=sanitise_log_dir,
        output_dir=output_dir,
        errors=errors,
        extra_months=transfer_result.affected_months,
    )
    categorised = outcome.categorised
    model_used = outcome.model_used
    categorisation_failed = outcome.categorisation_failed
    excel_path = outcome.excel_path
    drive_file_id = outcome.drive_file_id
    year_month = outcome.year_month
    drive_failed_month = outcome.drive_failed_month

    logger.info(
        "pipeline categorised %d rows via model %r",
        categorised,
        model_used or "(none)",
    )

    # ------------------------------------------------------------------
    # Notify — exactly one terminal notification, by severity priority.
    # Best-effort + fail-closed (see _notify / backend.notifier): a hard no-op
    # unless push is enabled with real VAPID keys AND a subscription exists, and
    # a notifier failure never breaks the run. Copy is counts/status only.
    #
    # Budget alerts (v6) are sent AFTER this terminal notification and are
    # ADDITIONAL to it (the "exactly one terminal notification" rule is unchanged).
    # check_budget_alerts is guarded and deduped once-per-month per category, so it
    # sends at most one per budgeted category and in practice nothing on a repeat.
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

    # Newly matched internal transfers are excluded from spending silently by the
    # store; tell the owner so the exclusion is never a mystery (count only — no
    # amounts, no descriptions). Re-runs create no new pairs, so this stays quiet.
    if transfer_result.pairs_created > 0:
        _notify(store, "transfer_detected", count=transfer_result.pairs_created)

    # Additional, guarded budget-threshold alerts for the latest data month. Only on
    # this non-noop path — a re-ingested identical file returns early above and stays
    # a pure no-op (no check, no claim, no send).
    check_budget_alerts(store)
    # Guarded recurring-merchant (subscription) detection over the latest data. Only on
    # this non-noop path; a re-ingested identical file never reaches it (see above).
    check_subscriptions(store)

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
        transfer_pairs=transfer_result.pairs_created,
    )


# ---------------------------------------------------------------------------
# Orphan recovery — re-run categorisation over NULL-category rows (no re-upload)
# ---------------------------------------------------------------------------


def retry_uncategorised(
    store,
    *,
    analyser_client=None,       # OpenRouterClient | fake; None → lazy-construct real one
    drive_service=None,         # injected Drive service; None → env-gated Drive
    sanitise_log_dir=None,      # forwarded to sanitise(log_dir=...); tests pass tmp_path
    output_dir=None,            # forwarded to build_workbook(output_dir=...); tests pass tmp_path
) -> dict:
    """Re-run categorisation over the store's NULL-category rows WITHOUT a re-upload.

    Owner-initiated orphan recovery (the app's "retry" button) for rows left
    uncategorised by an earlier failed / rate-limited run. Reuses the exact same
    _categorise_pending() path as the pipeline (sanitise -> gated few-shot preamble
    -> categorise -> set_categories -> rebuild the affected months' workbooks), so
    there is no divergent logic.

    The only off-machine call is inside categorise() (the analyser), which receives
    ONLY the sanitised (row_index, cleaned_description, amount) tuples. Never raises.

    Returns
    -------
    dict
      - No pending rows:            {"ok": True,  "categorised": 0, "remaining": 0}
      - Success:                    {"ok": True,  "categorised": n, "remaining": m}
      - OpenRouter down / all tiers failed (AnalyserError caught inside
        _categorise_pending): {"ok": False, "categorised": 0, "remaining": m,
                               "detail": "categoriser unavailable"}
    """
    if not store.uncategorised():
        return {"ok": True, "categorised": 0, "remaining": 0}

    errors: list[str] = []
    outcome = _categorise_pending(
        store,
        analyser_client=analyser_client,
        drive_service=drive_service,
        sanitise_log_dir=sanitise_log_dir,
        output_dir=output_dir,
        errors=errors,
    )

    if outcome.categorisation_failed:
        # OpenRouter down / all tiers failed — rows stay pending, safe status string.
        # Categories are unchanged on this branch, so no budget re-check is needed.
        return {
            "ok": False,
            "categorised": 0,
            "remaining": len(store.uncategorised()),
            "detail": "categoriser unavailable",
        }

    # A successful pass may have moved rows into budgeted categories — guarded check.
    check_budget_alerts(store)
    # Newly-categorised rows can also complete a recurring-merchant streak — guarded.
    check_subscriptions(store)

    return {
        "ok": True,
        "categorised": outcome.categorised,
        "remaining": len(store.uncategorised()),
    }
