"""fingerprint.py — pure, deterministic hashing functions for the three-layer idempotency
system (§7.4, FR-12..FR-15).

Layer 1 (FR-12) — file fingerprint
    SHA-256 of raw uploaded file bytes.  Used by the pipeline to skip processing an
    unchanged file entirely.

Layer 2 (FR-13) — transaction dedupe
    SHA-256 over a canonical serialisation of (date, amount, description, bank).
    Fingerprints are computed LOCALLY only and are never sent off-machine.  The raw
    description is used in the hash here because the hash stays on this machine and
    never leaves it; the §7.5 sanitiser handles what is safe to transmit.

Layer 3 (FR-14) — categorise only-new
    Of the just-ingested transactions, select only those not yet categorised so that the
    LLM call is skipped for already-known transactions.

FR-15 — no-op contract
    If every uploaded file fingerprint is already seen, the pipeline should not even build
    transactions.  If it does, an empty filter_new_transactions result (is_noop True)
    guarantees zero downstream work: no sanitise call, no LLM call, no store writes, no
    Excel/Drive output.  A re-run on unchanged input changes nothing.

No DB, no I/O, no network.  All functions are pure and deterministic.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Collection
from decimal import Decimal, ROUND_HALF_UP

from backend.data_source import Transaction

from .models import NewTxnResult

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_FIELD_SEP = "\x1f"  # ASCII Unit Separator — cannot occur in normalised text


# ---------------------------------------------------------------------------
# Layer 1 — file fingerprint (FR-12)
# ---------------------------------------------------------------------------

def file_fingerprint(data: bytes) -> str:
    """SHA-256 hex of raw uploaded file bytes. Stable; differs if any byte changes."""
    return hashlib.sha256(data).hexdigest()


def file_fingerprint_text(text: str) -> str:
    """Convenience: fingerprint of text encoded utf-8."""
    return file_fingerprint(text.encode("utf-8"))


def is_file_seen(fp: str, seen: Collection[str]) -> bool:
    """True if this file fingerprint was already processed (caller passes the known set)."""
    return fp in seen


# ---------------------------------------------------------------------------
# Layer 2 — transaction dedupe (FR-13)
# ---------------------------------------------------------------------------

def transaction_fingerprint(txn: Transaction) -> str:
    """Stable SHA-256 hex over canonical (date|amount|description|bank).

    Canonical serialisation (the dedupe contract — must not change):
      date_part   = txn.date.isoformat()                         → "YYYY-MM-DD"
      amount_part = str(quantize(amount, "0.01") + Decimal("0")) → sign-of-zero normalised
      desc_part   = collapse whitespace, strip ends, uppercase
      bank_part   = txn.bank.value                               → "commbank" / "westpac"
      canonical   = "\x1f".join([date_part, amount_part, desc_part, bank_part])

    Computed LOCALLY only; the raw description is hashed here but never transmitted.
    Nothing in this module performs a network call; the only thing that may leave the
    machine is the §7.5 SanitisedTxn, produced by the sanitiser stage.
    """
    date_part = txn.date.isoformat()

    # Quantize to 2 dp for amount-format stability; + Decimal("0") folds -0.00 → 0.00
    amount_part = str(
        txn.amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) + Decimal("0")
    )

    desc_part = _WS.sub(" ", txn.description).strip().upper()

    bank_part = txn.bank.value

    canonical = _FIELD_SEP.join([date_part, amount_part, desc_part, bank_part])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def filter_new_transactions(
    txns: list[Transaction],
    seen_fps: Collection[str],
) -> NewTxnResult:
    """Return only transactions whose fingerprint is not in seen_fps, also collapsing
    exact duplicates WITHIN the batch (first occurrence kept). Handles overlapping
    date-range exports. Order of first occurrence is preserved.

    Algorithm:
      seen_local: set[str] = set()    # fps emitted so far this batch
      for txn in txns:
          fp = transaction_fingerprint(txn)
          if fp in seen_fps:        continue                           # already in store
          if fp in seen_local:      duplicates += 1; continue         # dup within batch
          seen_local.add(fp); append (txn, fp)
    Build NewTxnResult from collected pairs.
    """
    # Normalise to set for O(1) membership even if caller passes a list
    seen: set[str] = set(seen_fps)

    seen_local: set[str] = set()
    collected_txns: list[Transaction] = []
    collected_fps: list[str] = []
    duplicates = 0

    for txn in txns:
        fp = transaction_fingerprint(txn)
        if fp in seen:
            continue                   # already in the persistent store
        if fp in seen_local:
            duplicates += 1
            continue                   # exact duplicate within this batch
        seen_local.add(fp)
        collected_txns.append(txn)
        collected_fps.append(fp)

    return NewTxnResult(
        new_transactions=tuple(collected_txns),
        fingerprints=tuple(collected_fps),
        duplicates_in_batch=duplicates,
    )


# ---------------------------------------------------------------------------
# Layer 3 — categorise only-new (FR-14)
# ---------------------------------------------------------------------------

def select_uncategorised(
    new_txns: list[Transaction],
    categorised_fps: Collection[str],
) -> list[Transaction]:
    """Of the just-ingested transactions, return only those whose fingerprint is NOT in
    categorised_fps — i.e. the ones that still need an LLM category. Preserves order.
    Storage-agnostic: the store decides what 'categorised' means and passes the fp set.
    Category persistence is NOT this module's job.
    """
    # Normalise to set for O(1) membership even if caller passes a list
    known: set[str] = set(categorised_fps)
    return [txn for txn in new_txns if transaction_fingerprint(txn) not in known]


# ---------------------------------------------------------------------------
# FR-15 — no-op contract helper
# ---------------------------------------------------------------------------

def is_noop(result: NewTxnResult) -> bool:
    """True when there is nothing new to ingest (len(result.new_transactions) == 0).

    CONTRACT for the pipeline (built later): if is_noop(result), skip sanitise, the LLM
    call, store writes, and Excel/Drive output — a run on unchanged input changes nothing.
    """
    return len(result.new_transactions) == 0
