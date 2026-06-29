"""models.py — output data structures for the idempotency stage (§7.4, FR-12..FR-15).

NewTxnResult : immutable batch-filter result returned by filter_new_transactions().
               Carries the genuinely-new transactions paired with their fingerprints and
               a count of within-batch duplicates that were collapsed.

The frozen dataclass prevents mutation after the dedupe check, matching the convention
used by SanitisedTxn / SanitiseResult in the sanitiser stage.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.data_source import Transaction


@dataclass(frozen=True)
class NewTxnResult:
    new_transactions: tuple[Transaction, ...]  # genuinely-new, batch-deduped, original order preserved
    fingerprints: tuple[str, ...]              # parallel to new_transactions; fp[i] == fingerprint of new_transactions[i]
    duplicates_in_batch: int                   # count of rows dropped because an identical fp appeared earlier IN THE SAME BATCH
    # Invariant: len(new_transactions) == len(fingerprints)
