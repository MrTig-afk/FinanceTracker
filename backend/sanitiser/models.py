"""models.py — output data structures for the sanitiser stage (§7.5, FR-16/FR-19/FR-20/FR-21).

SanitisedTxn   : the ONLY tuple allowed off-machine; exactly three fields (asserted by FR-16).
SanitiseResult : immutable batch result returned by sanitise(); also the source for the
                 local audit log.

Both are frozen dataclasses so the result cannot be mutated after the fail-closed check.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SanitisedTxn:
    row_index: int            # per-batch 0-based index into the input list; LOCAL re-join key.
                              # NOT an account/transaction ID.  This is the only "key" sent.
    cleaned_description: str  # scrubbed, fail-closed-verified, non-empty
    amount: Decimal           # carried through unchanged from Transaction.amount


@dataclass(frozen=True)
class SanitiseResult:
    payload: tuple[SanitisedTxn, ...]  # SAFE to send off-machine.  Nothing else may leave.
    dropped: tuple[int, ...]           # row_index values that failed closed (FR-21).
                                       # Contract: caller categorises these as "Other" LOCALLY.
                                       # They are OMITTED from payload.
    run_id: str                        # uuid4 hex for this batch; ties payload to the audit log
    timestamp: str                     # ISO-8601 UTC, datetime.now(timezone.utc).isoformat()
