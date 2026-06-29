"""core.py — orchestration: the public seam of the sanitiser stage (§7.5, FR-16..FR-21).

sanitise() is the single entry point for all other pipeline stages.  It reduces
each Transaction to a safe (row_index, cleaned_description, amount) triple, applies
the fail-closed residual re-scan, and writes a local audit log.

The analyser (§7.6) must be physically unable to obtain anything except
SanitiseResult.payload; it imports sanitise() from backend.sanitiser and uses
only that attribute.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from backend.data_source import Transaction

from .audit import resolve_log_dir, write_audit_log
from .models import SanitisedTxn, SanitiseResult
from .scrub import has_residual_identifier, scrub_description


def sanitise(
    transactions: list[Transaction],
    *,
    audit: bool = True,
    log_dir: str | os.PathLike | None = None,
) -> SanitiseResult:
    """Reduce each Transaction to a SAFE (row_index, cleaned_description, amount) triple.

    Parameters
    ----------
    transactions:
        The input list produced by the §7.3 parsers.  Never mutated (FR-19).
    audit:
        If True (default), write a local JSONL audit record.  Pass False (or a
        tmp_path) in tests to avoid littering ./logs.
    log_dir:
        Override the audit log directory.  None → resolve_log_dir() applies the
        override > $LOG_DIR > './logs' precedence.

    Returns
    -------
    SanitiseResult
        .payload  — tuple of SanitisedTxn; SAFE to send off-machine.
        .dropped  — tuple of row_index integers that failed closed (FR-21);
                    caller must categorise these as "Other" locally.
        .run_id   — uuid4 hex tying the return value to the audit record.
        .timestamp — ISO-8601 UTC string.

    Processing
    ----------
    For each transaction at position `idx` in `transactions`:
      1. cleaned = scrub_description(txn.description)
      2. if has_residual_identifier(cleaned) → append idx to dropped (omit from payload)
         else → append SanitisedTxn(idx, cleaned, txn.amount) to payload

    DROPPED rows CONSUME their index so all indexes map back to the caller's
    original list positions.  date and bank are NEVER carried into the result.

    Side effect (one, and only one): if `audit` is True, write_audit_log() is
    called with the resolved log_dir.  The log writes ONLY already-safe content.
    """
    run_id = uuid.uuid4().hex
    timestamp = datetime.now(timezone.utc).isoformat()

    payload_list: list[SanitisedTxn] = []
    dropped_list: list[int] = []

    for idx, txn in enumerate(transactions):
        cleaned = scrub_description(txn.description)
        if has_residual_identifier(cleaned):
            dropped_list.append(idx)
        else:
            payload_list.append(
                SanitisedTxn(
                    row_index=idx,
                    cleaned_description=cleaned,
                    amount=txn.amount,  # Decimal carried through unchanged
                )
            )

    result = SanitiseResult(
        payload=tuple(payload_list),
        dropped=tuple(dropped_list),
        run_id=run_id,
        timestamp=timestamp,
    )

    if audit:
        resolved = resolve_log_dir(log_dir)
        write_audit_log(result, log_dir=resolved)

    return result
