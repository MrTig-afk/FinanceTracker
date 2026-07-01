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

from backend.data_source import Bank, Transaction
from backend.idempotency import NewTxnResult, transaction_fingerprint

from .category_context import CategoryContext, DEFAULT_CONTEXT
from .fuel_rule import is_fuel_convenience
from .schema import init_schema as _schema_init
from .taxonomy import coerce_category

# Categories involved in the small-fuel-stop reclassification rule.
_FUEL_RULE_FROM = "Transport"
_FUEL_RULE_TO = "Dining Out"
# Strictly-under-$10 debit: -10.00 < amount < 0 (a spend smaller than ten dollars).
_FUEL_RULE_FLOOR = Decimal("-10.00")

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
# Order-agnostic opening/closing balance derivation (Q2)
# ---------------------------------------------------------------------------

# Cent tolerance for bank rounding in the running-balance relation.
_BALANCE_TOLERANCE = Decimal("0.01")


def _chain_result(
    seq: list[tuple[Decimal, Decimal | None]],
) -> tuple[Decimal, Decimal] | None:
    """Validate one candidate chronological sequence; return (opening, closing) or None.

    `seq` is a list of (amount, balance) pairs in a candidate chronological order.
    Valid iff every balance is non-null AND, for each consecutive pair,
    abs(balance[i] - (balance[i-1] + amount[i])) <= _BALANCE_TOLERANCE.
    """
    for _, balance in seq:
        if balance is None:
            return None
    for i in range(1, len(seq)):
        prev_balance = seq[i - 1][1]
        amount_i, balance_i = seq[i]
        if abs(balance_i - (prev_balance + amount_i)) > _BALANCE_TOLERANCE:  # type: ignore[operator]
            return None
    first_amount, first_balance = seq[0]
    opening = first_balance - first_amount  # type: ignore[operator]
    closing = seq[-1][1]
    return (opening, closing)


def _derive_account_balance(rows: list[sqlite3.Row]) -> dict[str, str | None]:
    """Order-agnostic opening/closing derivation for one account's month rows (Q2).

    `rows` are in insertion order (id ascending == CSV order); each row has
    'amount' and 'balance' TEXT columns ('balance' may be NULL). Chronological
    direction is NOT assumed from row order — both the ascending sequence and
    its reverse are checked against the running-balance relation, and whichever
    direction is internally consistent (or both, if they agree) is used.

    Returns {"opening": str|None, "closing": str|None}; both None when the
    direction cannot be determined (missing balance, inconsistent chain, or the
    two directions disagree) — the graceful "unavailable" fallback.
    """
    parsed = [
        (
            amount_from_text(r["amount"]),
            amount_from_text(r["balance"]) if r["balance"] is not None else None,
        )
        for r in rows
    ]

    if len(parsed) == 1:
        amount, balance = parsed[0]
        if balance is None:
            return {"opening": None, "closing": None}
        return {"opening": str(balance - amount), "closing": str(balance)}

    asc_result = _chain_result(parsed)
    desc_result = _chain_result(list(reversed(parsed)))

    if asc_result is not None and desc_result is not None:
        result = asc_result if asc_result == desc_result else None
    else:
        result = asc_result if asc_result is not None else desc_result

    if result is None:
        return {"opening": None, "closing": None}
    opening, closing = result
    return {"opening": str(opening), "closing": str(closing)}


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
        self._seed_category_context_if_empty()

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
    # Category context (D1 fixed taxonomy / D2 pre-filled example hints)
    # ------------------------------------------------------------------

    def _seed_category_context_if_empty(self) -> None:
        """Insert the 9 DEFAULT_CONTEXT rows when category_context is empty.

        Idempotent: only seeds an empty table, so a pre-existing DB is never
        re-seeded or duplicated. Runs for :memory:, tmp_path, and production DBs.
        """
        (count,) = self.conn.execute("SELECT COUNT(*) FROM category_context").fetchone()
        if count:
            return

        now = _utc_now_iso()
        self.conn.executemany(
            "INSERT INTO category_context(name, color, hints, position, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(c.name, c.color, c.hints, c.position, now) for c in DEFAULT_CONTEXT],
        )
        self.conn.commit()

    def get_category_context(self) -> list[CategoryContext]:
        """Return the 9 canonical categories ordered by position, with stored hints.

        After seeding, the table always has the 9 rows.
        """
        rows = self.conn.execute(
            "SELECT name, color, hints, position FROM category_context ORDER BY position"
        ).fetchall()
        return [
            CategoryContext(
                name=row["name"],
                color=row["color"],
                hints=row["hints"],
                position=row["position"],
            )
            for row in rows
        ]

    def save_category_context(self, hints_by_name: dict[str, str]) -> int:
        """Replace-all: rebuild the 9 canonical rows with hints from hints_by_name.

        name/color/position always come from DEFAULT_CONTEXT (canonical seed).
        Unknown names in hints_by_name are ignored; canonical names absent from
        the dict get hints=''. Never adds or removes categories (D1) — the table
        always ends up with exactly the 9 canonical rows. DELETE + INSERT in one
        transaction; commits once. Returns the number of rows written (9).
        """
        now = _utc_now_iso()
        rows = [
            (c.name, c.color, hints_by_name.get(c.name, ""), c.position, now)
            for c in DEFAULT_CONTEXT
        ]
        self.conn.execute("DELETE FROM category_context")
        self.conn.executemany(
            "INSERT INTO category_context(name, color, hints, position, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

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

        Inserts on the UNIQUE txn_fingerprint column; on conflict, upserts the
        balance column ONLY (never category) — see the Q3 upsert note in the spec.
        A double-run with identical (or missing) balances writes nothing (the
        WHERE clause evaluates false), preserving FR-15/FR-13 no-op behaviour.
        category is always NULL on initial insert.

        All rows are written in a single transaction; commits once before returning.

        Parameters
        ----------
        result:
            Output of idempotency.filter_new_transactions (or select_uncategorised).

        Returns
        -------
        int
            Number of rows actually inserted or balance-updated. 0 on a double-run
            with unchanged/null balances. 0 for an empty NewTxnResult.
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
                amount_to_text(txn.balance) if txn.balance is not None else None,
            )
            for i, txn in enumerate(result.new_transactions)
        ]

        self.conn.executemany(
            """
            INSERT INTO transactions
                (txn_fingerprint, date, description, amount, bank,
                 category, year_month, created_at, balance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(txn_fingerprint) DO UPDATE SET
                balance = excluded.balance
            WHERE excluded.balance IS NOT NULL
              AND (transactions.balance IS NULL OR transactions.balance IS NOT excluded.balance)
            """,
            rows,
        )
        self.conn.commit()

        return self.conn.total_changes - before

    def reconcile_balances(self, transactions: list[Transaction]) -> int:
        """Update balance-only for rows ALREADY in the store whose balance changed.

        The primary path for correcting a bank's running balance on a byte-different
        re-upload (Q3). For each txn: skip if txn.balance is None (a missing new
        balance must not wipe a stored one). Compute transaction_fingerprint(txn);
        if no stored row has that fingerprint, skip (that is a new row — add_new
        inserts it). If the stored balance already equals amount_to_text(txn.balance),
        skip (no write ⇒ idempotent). Otherwise UPDATE that row's balance column only
        — never category, never a new row, never a network call.

        All updates run in a single transaction; commits once before returning.

        Returns
        -------
        int
            Count of rows whose stored balance was actually changed.
        """
        if not transactions:
            return 0

        updated = 0
        for txn in transactions:
            if txn.balance is None:
                continue
            fp = transaction_fingerprint(txn)
            row = self.conn.execute(
                "SELECT balance FROM transactions WHERE txn_fingerprint = ?",
                (fp,),
            ).fetchone()
            if row is None:
                continue  # new row — add_new's job, not ours
            new_balance = amount_to_text(txn.balance)
            if row["balance"] == new_balance:
                continue  # unchanged — no write
            self.conn.execute(
                "UPDATE transactions SET balance = ? WHERE txn_fingerprint = ?",
                (new_balance, fp),
            )
            updated += 1

        self.conn.commit()
        return updated

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
    # Small-fuel-stop reclassification (view rule, reversible via marker)
    # ------------------------------------------------------------------

    def apply_fuel_dining_rule(self, year_month: str | None = None) -> int:
        """Move small fuel-stop spends from Transport to 'Dining Out' for one month.

        A row is moved when ALL hold:
          - it is currently categorised 'Transport';
          - its merchant is a fuel/convenience chain (is_fuel_convenience);
          - it is a debit strictly smaller than $10 (-10.00 < amount < 0).

        Moved rows are stamped reclassified_by_rule = 1 so revert_fuel_dining_rule()
        can restore exactly these and never touch a genuine 'Dining Out' row.

        Idempotent: a second call finds nothing still in 'Transport' to move.
        Defaults to latest_year_month(). Returns the number of rows moved.
        """
        if year_month is None:
            year_month = self.latest_year_month()
        if year_month is None:
            return 0

        rows = self.conn.execute(
            "SELECT id, description, amount FROM transactions "
            "WHERE year_month = ? AND category = ?",
            (year_month, _FUEL_RULE_FROM),
        ).fetchall()

        move_ids = [
            row["id"]
            for row in rows
            if is_fuel_convenience(row["description"])
            and _FUEL_RULE_FLOOR < amount_from_text(row["amount"]) < Decimal("0")
        ]
        if not move_ids:
            return 0

        self.conn.executemany(
            "UPDATE transactions SET category = ?, reclassified_by_rule = 1 WHERE id = ?",
            [(_FUEL_RULE_TO, i) for i in move_ids],
        )
        self.conn.commit()
        return len(move_ids)

    def revert_fuel_dining_rule(self, year_month: str | None = None) -> int:
        """Undo apply_fuel_dining_rule() for one month.

        Restores every row marked reclassified_by_rule = 1 back to 'Transport' and
        clears the marker. Rows the user or LLM genuinely put in 'Dining Out' are
        untouched (their marker is 0). Defaults to latest_year_month().
        Returns the number of rows restored.
        """
        if year_month is None:
            year_month = self.latest_year_month()
        if year_month is None:
            return 0

        cursor = self.conn.execute(
            "UPDATE transactions SET category = ?, reclassified_by_rule = 0 "
            "WHERE year_month = ? AND reclassified_by_rule = 1",
            (_FUEL_RULE_FROM, year_month),
        )
        self.conn.commit()
        return cursor.rowcount

    def fuel_rule_eligible(self, year_month: str | None = None) -> tuple[int, Decimal]:
        """Count + summed amount of transactions subject to the small-fuel-stop rule this month.

        A row is 'eligible' (stable across the toggle) when EITHER:
          - it is currently in Transport AND is_fuel_convenience(description)
            AND _FUEL_RULE_FLOOR < amount < 0   (not yet moved), OR
          - reclassified_by_rule == 1           (already moved by the rule).

        Returns (count, total_amount) where total_amount is the signed Decimal sum
        (negative debits). (0, Decimal('0.00')) when the month/db is empty.
        """
        if year_month is None:
            year_month = self.latest_year_month()
        if year_month is None:
            return (0, Decimal("0.00"))

        rows = self.conn.execute(
            "SELECT description, amount, category, reclassified_by_rule "
            "FROM transactions WHERE year_month = ?",
            (year_month,),
        ).fetchall()

        count = 0
        total = Decimal("0.00")
        for row in rows:
            amount = amount_from_text(row["amount"])
            already_moved = row["reclassified_by_rule"] == 1
            not_yet_moved = (
                row["category"] == _FUEL_RULE_FROM
                and is_fuel_convenience(row["description"])
                and _FUEL_RULE_FLOOR < amount < Decimal("0")
            )
            if already_moved or not_yet_moved:
                count += 1
                total += amount

        return (count, total)

    def fuel_rule_applied(self, year_month: str | None = None) -> bool:
        """True if any row in the month is currently reclassified by the fuel rule."""
        if year_month is None:
            year_month = self.latest_year_month()
        if year_month is None:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM transactions "
            "WHERE year_month = ? AND reclassified_by_rule = 1 LIMIT 1",
            (year_month,),
        ).fetchone()
        return row is not None

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

    def account_balances(self, year_month: str | None = None) -> dict:
        """Per-account opening/closing balance for a month (LOCAL-ONLY, never sent off-machine).

        Returns { "commbank": {"opening": str|None, "closing": str|None},
                  "westpac":  {"opening": str|None, "closing": str|None} }
        Only includes a bank key when that bank has >=1 row in the month.
        'null' opening/closing means "could not be determined" — the caller
        renders this as an unavailable figure, never a guessed number.
        All money values are str(Decimal) or None — never float.

        Defaults to latest_year_month(); empty DB / no month -> {}.

        Order-agnostic derivation (Q2): row insertion order (id ascending) is CSV
        order, but CSV order is NOT assumed to be chronological. Instead, both the
        ascending and reversed sequences are checked against the running-balance
        relation `abs(balance[i] - (balance[i-1] + amount[i])) <= 0.01`; whichever
        direction holds (and only that one, or both agreeing) is used to derive
        opening/closing. Otherwise the account is reported unavailable.
        """
        if year_month is None:
            year_month = self.latest_year_month()
        if year_month is None:
            return {}

        result: dict[str, dict[str, str | None]] = {}
        for bank in (Bank.COMMBANK.value, Bank.WESTPAC.value):
            rows = self.conn.execute(
                "SELECT amount, balance FROM transactions "
                "WHERE year_month = ? AND bank = ? ORDER BY id",
                (year_month, bank),
            ).fetchall()
            if not rows:
                continue
            result[bank] = _derive_account_balance(rows)
        return result

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
                "fuel_rule_applied": false,  # small-fuel-stop rule active this month?
                "fuel_rule_eligible": 3,  # count of rows subject to the fuel-stop rule
                "fuel_rule_eligible_amount": "-24.10",  # signed Decimal sum, as str
                "account_balances": {"commbank": {"opening": "1000.00", "closing": "918.10"}},
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
                "fuel_rule_applied": False,
                "fuel_rule_eligible": 0,
                "fuel_rule_eligible_amount": "0.00",
                "account_balances": {},
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

        count, amt = self.fuel_rule_eligible(year_month)

        return {
            "year_month": year_month,
            "totals": {k: str(v) for k, v in totals.items()},
            "net": str(net),
            "count": len(rows),
            "fuel_rule_applied": self.fuel_rule_applied(year_month),
            "fuel_rule_eligible": count,
            "fuel_rule_eligible_amount": str(amt),
            "account_balances": self.account_balances(year_month),
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

    # ------------------------------------------------------------------
    # Period views (v2 Pass 1): Monthly / Yearly + period-over-period
    # comparison. Read-only aggregation over the existing schema — no new
    # tables, no write-path change. Money contract copies summary() exactly:
    # Decimal accumulation in Python, str(Decimal) out, NULL category ->
    # "Uncategorised", defined empty shape when there is no data.
    # ------------------------------------------------------------------

    def available_months(self) -> list[str]:
        """All distinct 'YYYY-MM' present, DESCENDING (latest first). [] when empty."""
        rows = self.conn.execute(
            "SELECT DISTINCT year_month FROM transactions ORDER BY year_month DESC"
        ).fetchall()
        return [row[0] for row in rows]

    def available_years(self) -> list[str]:
        """All distinct 'YYYY' present, DESCENDING. [] when empty."""
        rows = self.conn.execute(
            "SELECT DISTINCT substr(year_month, 1, 4) AS y FROM transactions ORDER BY 1 DESC"
        ).fetchall()
        return [row[0] for row in rows]

    def _totals_for(self, where_sql: str, param: str) -> tuple[dict[str, Decimal], Decimal, int]:
        """Accumulate (totals-by-category, net, count) for one WHERE clause + param.

        Same Decimal accumulation loop as summary(); NULL category -> "Uncategorised".
        `where_sql` must reference exactly one '?' placeholder, bound to `param`.
        """
        rows = self.conn.execute(
            f"SELECT category, amount FROM transactions WHERE {where_sql}",
            (param,),
        ).fetchall()

        totals: dict[str, Decimal] = {}
        net = Decimal("0.00")
        for row in rows:
            cat = row["category"] if row["category"] is not None else "Uncategorised"
            amt = amount_from_text(row["amount"])
            totals[cat] = totals.get(cat, Decimal("0.00")) + amt
            net += amt

        return totals, net, len(rows)

    def _compare(
        self, current: dict[str, Decimal], previous: dict[str, Decimal]
    ) -> list[dict]:
        """Build comparison rows: union of categories, delta = current - previous.

        pct_change = (current - previous) / previous * 100 using the SIGNED previous
        as denominator, rounded to 1dp as a float; None when previous == 0 (including
        a category absent from the previous period). Ordered by abs(current) DESC,
        tie-broken by category name ASC (mirrors summary.js#categoryRows).
        """
        entries: list[tuple[str, Decimal, Decimal, Decimal, float | None]] = []
        for cat in set(current) | set(previous):
            cur = current.get(cat, Decimal("0.00"))
            prev = previous.get(cat, Decimal("0.00"))
            delta = cur - prev
            pct: float | None
            if prev == 0:
                pct = None
            else:
                pct = round(float(delta) / float(prev) * 100, 1)
            entries.append((cat, cur, prev, delta, pct))

        entries.sort(key=lambda e: (-abs(e[1]), e[0]))

        return [
            {
                "category": cat,
                "current": str(cur),
                "previous": str(prev),
                "delta": str(delta),
                "pct_change": pct,
            }
            for cat, cur, prev, delta, pct in entries
        ]

    def month_view(self, ym: str | None = None) -> dict:
        """Breakdown + month-over-month comparison for one month.

        ym defaults to the latest populated month (available_months()[0]).
        'Previous' = the greatest populated month strictly < ym (skip gaps),
        from available_months(); None if there is no earlier populated month.
        Empty DB -> the defined empty shape (see spec).
        """
        months = self.available_months()
        if ym is None:
            ym = months[0] if months else None

        if ym is None:
            return {
                "period": "month",
                "ym": None,
                "prev_ym": None,
                "totals": {},
                "net": "0.00",
                "count": 0,
                "comparison": [],
                "available_months": [],
            }

        totals, net, count = self._totals_for("year_month = ?", ym)
        prev_ym = next((m for m in months if m < ym), None)
        prev_totals = self._totals_for("year_month = ?", prev_ym)[0] if prev_ym else {}

        return {
            "period": "month",
            "ym": ym,
            "prev_ym": prev_ym,
            "totals": {k: str(v) for k, v in totals.items()},
            "net": str(net),
            "count": count,
            "comparison": self._compare(totals, prev_totals),
            "available_months": months,
        }

    def year_view(self, y: str | None = None) -> dict:
        """Breakdown + year-over-year comparison for one year.

        y defaults to the latest populated year. 'Previous' = greatest populated
        year strictly < y (skip gaps); None if none earlier. Empty DB -> the
        defined empty shape (see spec).
        """
        years = self.available_years()
        if y is None:
            y = years[0] if years else None

        if y is None:
            return {
                "period": "year",
                "y": None,
                "prev_y": None,
                "totals": {},
                "net": "0.00",
                "count": 0,
                "comparison": [],
                "available_years": [],
            }

        totals, net, count = self._totals_for("year_month LIKE ?", f"{y}-%")
        prev_y = next((yy for yy in years if yy < y), None)
        prev_totals = (
            self._totals_for("year_month LIKE ?", f"{prev_y}-%")[0] if prev_y else {}
        )

        return {
            "period": "year",
            "y": y,
            "prev_y": prev_y,
            "totals": {k: str(v) for k, v in totals.items()},
            "net": str(net),
            "count": count,
            "comparison": self._compare(totals, prev_totals),
            "available_years": years,
        }
