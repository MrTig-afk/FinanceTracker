"""audit.py — local audit log for the sanitiser stage (§7.5, FR-20).

Writes a JSONL record describing the EXACT payload that would be sent off-machine.
Only already-safe sanitised tuples + run metadata are written — NEVER raw descriptions,
dates, bank identifiers, balances, or any dropped-row text.

LOG_DIR is read from .env via python-dotenv; it is never hardcoded.
The log directory is created on first write (not on import) so a simple
`import backend.sanitiser` never touches the filesystem.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .models import SanitiseResult


def resolve_log_dir(override: str | os.PathLike | None = None) -> Path:
    """Resolve the audit log directory.

    Priority: override argument > $LOG_DIR environment variable > './logs'.
    Does NOT create the directory — that is the responsibility of write_audit_log().
    """
    if override is not None:
        return Path(override)
    load_dotenv()  # no-op if already loaded; safe to call multiple times
    log_dir = os.getenv("LOG_DIR", "./logs")
    return Path(log_dir)


def write_audit_log(result: SanitiseResult, *, log_dir: Path) -> Path:
    """Append one JSONL record describing the EXACT payload that would be sent.

    Creates log_dir (parents=True, exist_ok=True) if it does not already exist.
    Returns the path to the audit file.

    File: <log_dir>/sanitiser-audit.jsonl

    Record schema (the ONLY things written — all are by-definition safe):
    {
        "run_id": "...",
        "timestamp": "...ISO UTC...",
        "sent_count": 12,
        "dropped_count": 1,
        "payload": [
            {"row_index": 0, "cleaned_description": "WOOLWORTHS", "amount": "-55.73"},
            ...
        ],
        "dropped_row_index": [7]
    }

    Constraints:
    - amount is serialised as str(Decimal) — NEVER float (preserves exactness).
    - Only cleaned_description (already scrubbed + fail-closed) is written; the
      raw Transaction.description, date, bank, and any dropped-row text are NEVER
      written to the log.
    - Dropped rows appear only as bare row_index integers in dropped_row_index.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "sanitiser-audit.jsonl"

    record = {
        "run_id": result.run_id,
        "timestamp": result.timestamp,
        "sent_count": len(result.payload),
        "dropped_count": len(result.dropped),
        "payload": [
            {
                "row_index": txn.row_index,
                "cleaned_description": txn.cleaned_description,
                "amount": str(txn.amount),  # str(Decimal) — never float
            }
            for txn in result.payload
        ],
        "dropped_row_index": list(result.dropped),
    }

    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")

    return log_path
