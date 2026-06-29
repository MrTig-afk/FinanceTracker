"""store.py — SQLite-backed durable archive for FinanceTracker (§7.7, FR-27..FR-29).

The store is the persistence backbone: it archives clean, dated, categorised transaction
rows plus Layer-1/Layer-2 fingerprints. It supplies the seen-fingerprint sets that the
idempotency layer consumes, and persists the results the pipeline produces.

Privacy note
------------
Raw transaction descriptions, amounts, and bank identifiers are SENSITIVE.
They are stored LOCALLY only and NEVER sent off-machine.
This module contains ZERO network code; no requests, no URLs, nothing off-machine.

Secrets
-------
SQLITE_PATH is read from .env via python-dotenv; never hardcoded anywhere.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from dotenv import load_dotenv

from backend.idempotency import NewTxnResult

from .schema import init_schema as _schema_init
from .taxonomy import coerce_category

# ---------------------------------------------------------------------------
# Decimal <-> TEXT helpers
# ---------------------------------------------------------------------------

_TWO_DP = Decimal("0.01")


def amount_to_text(amount: Decimal) -> str:
    """Canonical storage form: quantize to 2 dp, fold -0.00 -> 0.00, str().

    Same quantize convention as idempotency/fingerprint.py so the stored amount
    matches the fingerprinted amount. Never use float anywhere in the money path.
    """
    return str(amount.quantize(_TWO_DP, rounding=ROUND_HALF_UP) + Decimal("0"))


def amount_from_text(text: str) -> Decimal:
    """Parse a stored TEXT amount back to an exact Decimal. Never use float."""
    return Decimal(text)


# ---------------------------------------------------------------------------
# UTC timestamp helper (module-level; no IO)
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp string suitable for created_at / processed_at."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Return row types (frozen dataclasses — mirror NewTxnResult style)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UncategorisedRow:
    """A transaction row that still needs a category (category IS NULL in the DB).

    Carries both id and txn_fingerprint so the caller can write categories back
    by either key via set_categories().

    description is RAW, local-only — it MUST NOT be sent off-machine directly.
    Only the §7.5 sanitiser output (SanitisedTxn) may travel off-machine.
    """

    id: int
    txn_fingerprint: str
    date: str           # ISO 'YYYY-MM-DD'
    description: str    # RAW, local-only
    amount: Decimal
    bank: str           # Bank.value ('commbank' | 'westpac')


@dataclass(frozen=True)
class MonthRow:
    """A single transaction row formatted for the Excel builder (FR-30).

    description is RAW, local-only — stays on this machine.
    amount is Decimal (exact). category may be None if not yet categorised.
    """

    date: str           # ISO 'YYYY-MM-DD'
    description: str    # RAW, local-only
    amount: Decimal
    category: str | None


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_db_path(override: str | os.PathLike | None = None) -> str:
    """Resolve the SQLite database path without creating any file or directory.

    Priority: override argument > $SQLITE_PATH env var > './data/financetracker.sqlite'.

    ':memory:' and any tmp_path file pass through untouched.
    Calls load_dotenv() (no-op if already loaded) to pick up SQLITE_PATH from .env.
    Does NOT create the file or its parent directory — that is the caller's responsibility
    (Store accepts create_parents=True for production use).
    """
    if override is not None:
        return str(override)
    load_dotenv()  # no-op if already loaded; safe to call multiple times
    return os.getenv("SQLITE_PATH", "./data/financetracker.sqlite")


# ---------------------------------------------------------------------------
# Store class
# ---------------------------------------------------------------------------

class Store:
    """Local durable archive: one sqlite3.Connection wrapping one database file.

    Sensitive raw data (descriptions, amounts, dates, bank identifiers) is stored
    LOCALLY only. This class contains ZERO network code; nothing it holds may leave
    the machine.

    Usage (context manager — recommended)::

        with Store(":memory:") as store:
            store.add_new(result)
            totals = store.summary()

    Usage (manual — each write method commits before returning)::

        store = Store(create_parents=True)   # uses SQLITE_PATH from .env
        store.add_new(result)
        store.close()

    Parameters
    ----------
    path:
        Path to the SQLite database. Defaults to SQLITE_PATH from .env or
        './data/financetracker.sqlite'. Pass ':memory:' for in-process tests.
    create_parents:
        When True AND path is a real file (not ':memory:'), create any missing
        parent directories. Defaults to False so that importing this module and
        constructing Store in tests never creates ./data/ accidentally.
    """

    def __init__(
        self,
        path: str | os.PathLike | None = None,
        *,
        create_parents: bool = False,
    ) -> None:
        self._path = resolve_db_path(path)

        if create_parents and self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

        self.conn: sqlite3.Connection = sqlite3.connect(self._path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        _schema_init(self.conn)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            self.conn.commit()
        self.conn.close()

    def close(self) -> None:
        """Commit any pending changes and close the connection."""
        self.conn.commit()
        self.conn.close()

    # ------------------------------------------------------------------
    # Schema (convenience instance wrapper)
    # ------------------------------------------------------------------

    def init_schema(self) -> None:
        """Re-run the DDL against this connection; idempotent (CREATE TABLE IF NOT EXISTS)."""
        _schema_init(self.conn)

    # ------------------------------------------------------------------
    # Layer 1: file fingerprints (FR-12)
    # ------------------------------------------------------------------

    def is_file_processed(self, fp: str) -> bool:
        """True if this file fingerprint is already recorded in file_fingerprints."""
        row = self.conn.execute(
            "SELECT 1 FROM file_fingerprints WHERE fingerprint = ? LIMIT 1",
            (fp,),
        ).fetchone()
        return row is not None

    def mark_file_processed(self, fp: str) -> None:
        """Record a file fingerprint; INSERT OR IGNORE so a double call is a no-op."""
        self.conn.execute(
            "INSERT OR IGNORE INTO file_fingerprints(fingerprint, processed_at) VALUES (?, ?)",
            (fp, _utc_now_iso()),
        )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Layer 2: transaction dedupe (FR-13)
    # ------------------------------------------------------------------

    def seen_transaction_fingerprints(self) -> set[str]:
        """Return all stored transaction fingerprints as a set.

        This set feeds idempotency.filter_new_transactions so it can exclude rows
        already present in the database. Returns an empty set when the DB is empty.
        """
        rows = self.conn.execute(
            "SELECT txn_fingerprint FROM transactions"
        ).fetchall()
        return {row[0] for row in rows}

    def add_new(self, result: NewTxnResult) -> int:
        """Persist genuinely-new rows from a NewTxnResult.

        Uses INSERT OR IGNORE on the UNIQUE txn_fingerprint column so a double-run
        never duplicates rows (FR-15, FR-13). category is always NULL on initial insert.

        All rows are written in a single transaction; commits once before returning.

        Parameters
        ----------
        result:
            Output of idempotency.filter_new_transactions (or select_uncategorised).

        Returns
        -------
        int
            Number of rows actually inserted. 0 on a double-run (all fingerprints
            already present). 0 for an empty NewTxnResult.
        """
        if not result.new_transactions:
            return 0

        before = self.conn.total_changes

        rows = [
            (
                result.fingerprints[i],
                txn.date.isoformat(),               # date 'YYYY-MM-DD'
                txn.description,                    # RAW — never sent off-machine
                amount_to_text(txn.amount),         # TEXT canonical 2dp
                txn.bank.value,                     # 'commbank' | 'westpac'
                None,                               # category — NULL until categorised
                txn.date.isoformat()[:7],           # year_month 'YYYY-MM'
                _utc_now_iso(),                     # created_at UTC
            )
            for i, txn in enumerate(result.new_transactions)
        ]

        self.conn.executemany(
            """
            INSERT OR IGNORE INTO transactions
                (txn_fingerprint, date, description, amount, bank,
                 category, year_month, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

        return self.conn.total_changes - before

    # ------------------------------------------------------------------
    # Layer 3: categorise only-new (FR-14)
    # ------------------------------------------------------------------

    def categorised_fingerprints(self) -> set[str]:
        """Return fingerprints of all transactions that already have a non-NULL category.

        Feeds idempotency.select_uncategorised so that the pipeline skips the LLM
        call for rows whose category is already known.
        """
        rows = self.conn.execute(
            "SELECT txn_fingerprint FROM transactions WHERE category IS NOT NULL"
        ).fetchall()
        return {row[0] for row in rows}

    def uncategorised(self) -> list[UncategorisedRow]:
        """Return all transactions with category IS NULL, ordered by id.

        Carries both id and txn_fingerprint so the caller can write categories back
        by either key via set_categories(). amount is returned as Decimal.
        """
        rows = self.conn.execute(
            """
            SELECT id, txn_fingerprint, date, description, amount, bank
              FROM transactions
             WHERE category IS NULL
             ORDER BY id
            """
        ).fetchall()
        return [
            UncategorisedRow(
                id=row["id"],
                txn_fingerprint=row["txn_fingerprint"],
                date=row["date"],
                description=row["description"],
                amount=amount_from_text(row["amount"]),
                bank=row["bank"],
            )
            for row in rows
        ]

    def set_categories(self, mapping: dict[str | int, str]) -> int:
        """Update category for rows identified by txn_fingerprint (str) or row id (int).

        Key type detection: isinstance(key, int) -> update by id; str -> by fingerprint.
        Each label is passed through coerce_category (unknown/None/'' -> 'Other').
        All updates run in a single transaction; commits once before returning.

        Parameters
        ----------
        mapping:
            {fingerprint_or_id: category_label, ...}. Mixed key types are fine.

        Returns
        -------
        int
            Total number of rows actually updated. Keys not found in the table
            contribute 0 to the count.
        """
        updated = 0
        for key, label in mapping.items():
            category = coerce_category(label)
            if isinstance(key, int):
                cursor = self.conn.execute(
                    "UPDATE transactions SET category = ? WHERE id = ?",
                    (category, key),
                )
            else:
                cursor = self.conn.execute(
                    "UPDATE transactions SET category = ? WHERE txn_fingerprint = ?",
                    (category, key),
                )
            updated += cursor.rowcount
        self.conn.commit()
        return updated

    # ------------------------------------------------------------------
    # Reporting (dashboard / Excel builder)
    # ------------------------------------------------------------------

    def latest_year_month(self) -> str | None:
        """Return the most recent 'YYYY-MM' present in the transactions table.

        Returns None when the database is empty.
        """
        row = self.conn.execute(
            "SELECT MAX(year_month) FROM transactions"
        ).fetchone()
        if row is None:
            return None
        return row[0]  # None when no rows exist

    def summary(self, year_month: str | None = None) -> dict:
        """Return a categorised spending summary for a given month.

        Defaults to latest_year_month() when year_month is None.
        Returns the empty shape when the database is empty.

        Amounts are summed in Python with Decimal (NOT SQL SUM) for exactness.
        NULL-category rows appear under the literal key 'Uncategorised' in totals.
        All money values in the returned dict are str(Decimal) — never float.

        Returns
        -------
        dict with shape::

            {
                "year_month": "YYYY-MM" | None,
                "totals": {"Groceries": "-123.45", ...},  # only categories present
                "net": "-50.00",
                "count": 12,
            }
        """
        if year_month is None:
            year_month = self.latest_year_month()

        if year_month is None:
            # Empty database
            return {
                "year_month": None,
                "totals": {},
                "net": "0.00",
                "count": 0,
            }

        rows = self.conn.execute(
            "SELECT category, amount FROM transactions WHERE year_month = ?",
            (year_month,),
        ).fetchall()

        totals: dict[str, Decimal] = {}
        net = Decimal("0.00")

        for row in rows:
            cat = row["category"] if row["category"] is not None else "Uncategorised"
            amt = amount_from_text(row["amount"])
            totals[cat] = totals.get(cat, Decimal("0.00")) + amt
            net += amt

        return {
            "year_month": year_month,
            "totals": {k: str(v) for k, v in totals.items()},
            "net": str(net),
            "count": len(rows),
        }

    def transactions_for_month(self, year_month: str | None = None) -> list[MonthRow]:
        """Return all rows for a month ordered by date then id.

        Defaults to latest_year_month(). Returns [] when the database is empty.
        amount is Decimal; category is None when not yet categorised.
        Intended for the Excel builder (FR-30): Date / Description / Amount / Category.
        """
        if year_month is None:
            year_month = self.latest_year_month()

        if year_month is None:
            return []

        rows = self.conn.execute(
            """
            SELECT date, description, amount, category
              FROM transactions
             WHERE year_month = ?
             ORDER BY date, id
            """,
            (year_month,),
        ).fetchall()

        return [
            MonthRow(
                date=row["date"],
                description=row["description"],
                amount=amount_from_text(row["amount"]),
                category=row["category"],
            )
            for row in rows
        ]
