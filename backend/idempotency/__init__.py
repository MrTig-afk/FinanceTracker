"""idempotency — three-layer dedupe and fingerprinting for FinanceTracker (§7.4, FR-12..FR-15).

Public API
----------
NewTxnResult            : immutable batch-filter result; pairs new transactions with their
                          fingerprints and records within-batch duplicate count.

file_fingerprint        : SHA-256 hex of raw file bytes (Layer 1, FR-12).  Used to skip
                          unchanged files before even building Transaction objects.
file_fingerprint_text   : convenience wrapper — fingerprints a str encoded as utf-8.
is_file_seen            : True when a file fingerprint is already in the processed set.

transaction_fingerprint : SHA-256 hex over canonical (date|amount|description|bank)
                          (Layer 2, FR-13).  Stable across runs; used as the dedupe key
                          in the persistent store (built in §7.7).
filter_new_transactions : given a batch of Transaction objects and the set of already-seen
                          fingerprints, return only genuinely-new rows with within-batch
                          duplicates collapsed (first occurrence kept).
select_uncategorised    : of the just-ingested transactions, return only those not yet
                          categorised — skips the LLM call for already-known rows
                          (Layer 3, FR-14).
is_noop                 : True when filter_new_transactions returned nothing new (FR-15).
                          Pipeline contract: if True, skip sanitise, LLM, store, and output.

Privacy note
------------
All fingerprints are computed LOCALLY only and are never sent off-machine.  They are
opaque SHA-256 hex digests.  Nothing in this package performs a network call; the only
thing that may leave the machine is the §7.5 SanitisedTxn, produced by the sanitiser.
"""

from .fingerprint import (
    file_fingerprint,
    file_fingerprint_text,
    filter_new_transactions,
    is_file_seen,
    is_noop,
    select_uncategorised,
    transaction_fingerprint,
)
from .models import NewTxnResult

__all__ = [
    "NewTxnResult",
    "file_fingerprint",
    "file_fingerprint_text",
    "is_file_seen",
    "transaction_fingerprint",
    "filter_new_transactions",
    "select_uncategorised",
    "is_noop",
]
