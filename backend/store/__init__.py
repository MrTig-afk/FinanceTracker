"""store — local durable archive for FinanceTracker (§7.7, FR-27..FR-29).

Provides the SQLite persistence backbone:

  Layer 1 (FR-12)  — file-fingerprint tracking to skip re-uploaded identical files.
  Layer 2 (FR-13)  — transaction storage with fingerprint-based dedupe
                     (INSERT OR IGNORE on UNIQUE txn_fingerprint).
  Layer 3 (FR-14)  — category persistence and retrieval; supplies categorised_fps
                     so the pipeline skips the LLM call for already-known rows.
  Reporting         — monthly summary and per-row listing for the Excel builder (FR-30).

Privacy note
------------
ALL data handled by this module is SENSITIVE: raw descriptions, amounts, dates, and
bank identifiers NEVER leave this machine and are NEVER committed to git. The SQLite
database is gitignored. This module contains ZERO network code. Only the §7.5
sanitiser's SanitisedTxn output (cleaned_description, amount, row_index) may travel
off-machine via the §7.6 analyser — nothing here ever does.

Secrets
-------
SQLITE_PATH is read from .env via python-dotenv. Never hardcoded. No connection is
opened and no file is created by a bare ``import backend.store``.
"""
from __future__ import annotations

from .category_context import CategoryContext, DEFAULT_CONTEXT
from .schema import init_schema
from .store import (
    MonthRow,
    Store,
    UncategorisedRow,
    amount_from_text,
    amount_to_text,
    resolve_db_path,
)
from .taxonomy import TAXONOMY, OTHER, coerce_category

__all__ = [
    # Core class and path resolver
    "Store",
    "resolve_db_path",
    # Return row types
    "UncategorisedRow",
    "MonthRow",
    # Schema free function (for callers that manage their own sqlite3.Connection)
    "init_schema",
    # Taxonomy
    "TAXONOMY",
    "OTHER",
    "coerce_category",
    # Category context (D1 fixed taxonomy / D2 pre-filled example hints)
    "CategoryContext",
    "DEFAULT_CONTEXT",
    # Money helpers — same convention as audit.py; useful to Excel builder and tests
    "amount_to_text",
    "amount_from_text",
]
