"""test_store.py — pytest suite for the §7.7 SQLite store.

ALL fixtures use synthetic data generated inline.
No real transactions, no real descriptions, no real account numbers.
Never reads data/inbox/* or any tracked CSV file.
No network calls anywhere in this file.
Every database is :memory: or tmp_path — NEVER the real SQLITE_PATH / ./data/.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.data_source import Bank, Transaction
from backend.idempotency import (
    NewTxnResult,
    filter_new_transactions,
    transaction_fingerprint,
)
from backend.store import (
    TAXONOMY,
    MonthRow,
    Store,
    UncategorisedRow,
    amount_from_text,
    amount_to_text,
    coerce_category,
    init_schema,
    resolve_db_path,
)
from backend.store.store import _month_range

# ---------------------------------------------------------------------------
# Synthetic helpers — invented values; never real data
# ---------------------------------------------------------------------------

_SYNTH_DATE = date(2026, 6, 10)  # fixed synthetic date; not meaningful


def _txn(
    desc: str = "SYNTH MERCHANT A",
    amount: str = "-10.00",
    d: date = _SYNTH_DATE,
    bank: Bank = Bank.COMMBANK,
    balance: str | None = None,
) -> Transaction:
    """Build a synthetic Transaction without touching any real data."""
    return Transaction(
        date=d,
        description=desc,
        amount=Decimal(amount),
        bank=bank,
        balance=Decimal(balance) if balance is not None else None,
    )


def _result(*txns: Transaction) -> NewTxnResult:
    """Build a NewTxnResult from synthetic transactions with real fingerprints."""
    fps = tuple(transaction_fingerprint(t) for t in txns)
    return NewTxnResult(
        new_transactions=tuple(txns),
        fingerprints=fps,
        duplicates_in_batch=0,
    )


# ---------------------------------------------------------------------------
# TestSchemaIdempotent
# ---------------------------------------------------------------------------


class TestSchemaIdempotent:
    def test_init_schema_idempotent_on_file(self, tmp_path) -> None:
        """Two Store objects on the same file + repeated init_schema must not raise."""
        db_path = str(tmp_path / "t.sqlite")

        store1 = Store(db_path)
        store1.init_schema()
        store1.init_schema()
        store1.close()

        store2 = Store(db_path)
        store2.init_schema()
        store2.close()

    def test_init_schema_free_function_idempotent(self) -> None:
        """The free-function init_schema called twice on same connection must not raise."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        init_schema(conn)
        init_schema(conn)  # second call must be a no-op
        # Verify both expected tables exist
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "transactions" in tables
        assert "file_fingerprints" in tables
        conn.close()

    def test_memory_store_has_tables(self) -> None:
        """A fresh :memory: Store has the expected tables immediately after construction."""
        with Store(":memory:") as store:
            store.conn.execute("SELECT 1 FROM transactions LIMIT 1")
            store.conn.execute("SELECT 1 FROM file_fingerprints LIMIT 1")


# ---------------------------------------------------------------------------
# TestIdempotentInsert (FR-13, FR-15)
# ---------------------------------------------------------------------------


class TestIdempotentInsert:
    def test_idempotent_insert_no_duplicate(self) -> None:
        """add_new twice with the same NewTxnResult inserts each row exactly once (FR-15).

        Second call returns 0 and row count stays equal to len(txns).
        Guaranteed by UNIQUE(txn_fingerprint) + INSERT OR IGNORE.
        """
        t1 = _txn("SYNTH GROCERY STORE", amount="-25.00", d=date(2026, 6, 1))
        t2 = _txn("SYNTH FUEL STATION", amount="-80.00", d=date(2026, 6, 2))
        r = _result(t1, t2)

        with Store(":memory:") as store:
            first_count = store.add_new(r)
            second_count = store.add_new(r)

            row_count = store.conn.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]

        assert first_count == 2, "first call should insert 2 rows"
        assert second_count == 0, "second call on unchanged input must insert 0 (FR-15)"
        assert row_count == 2, "total row count must remain 2"

    def test_empty_new_txn_result_is_noop(self) -> None:
        """add_new with empty NewTxnResult returns 0; no rows inserted; no commit error."""
        empty = NewTxnResult(new_transactions=(), fingerprints=(), duplicates_in_batch=0)
        with Store(":memory:") as store:
            result = store.add_new(empty)
            count = store.conn.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]

        assert result == 0
        assert count == 0

    def test_add_new_stores_correct_field_values(self) -> None:
        """Persisted rows carry correct date, description, amount, bank, year_month."""
        t = _txn("SYNTH RETAILER", amount="-45.99", d=date(2026, 6, 15), bank=Bank.WESTPAC)
        r = _result(t)

        with Store(":memory:") as store:
            store.add_new(r)
            row = store.conn.execute(
                "SELECT date, description, amount, bank, year_month, category "
                "FROM transactions"
            ).fetchone()

        assert row["date"] == "2026-06-15"
        assert row["description"] == "SYNTH RETAILER"
        assert row["amount"] == "-45.99"
        assert row["bank"] == "westpac"
        assert row["year_month"] == "2026-06"
        assert row["category"] is None


# ---------------------------------------------------------------------------
# TestSeenFingerprintsRoundtrip
# ---------------------------------------------------------------------------


class TestSeenFingerprintsRoundtrip:
    def test_seen_fingerprints_roundtrip_with_idempotency(self) -> None:
        """Round-trip: stored fps fed to filter_new_transactions skip already-stored rows.

        Proves the Layer-2 contract that bridges §7.4 and §7.7 (FR-13, FR-15).
        A re-run on unchanged input is a no-op; only genuinely new rows pass through.
        """
        t1 = _txn("SYNTH CAFE ONE", amount="-8.50", d=date(2026, 6, 3))
        t2 = _txn("SYNTH BAKERY TWO", amount="-12.00", d=date(2026, 6, 4))
        t_new = _txn("SYNTH GYM THREE", amount="-45.00", d=date(2026, 6, 5))

        with Store(":memory:") as store:
            # Store t1 and t2
            store.add_new(_result(t1, t2))

            seen = store.seen_transaction_fingerprints()
            assert len(seen) == 2

            # Batch re-includes t1 and t2 (already stored) plus one genuinely new txn
            batch = [t1, t2, t_new]
            new_result = filter_new_transactions(batch, seen)

            # Only t_new survives filtering
            assert len(new_result.new_transactions) == 1
            assert new_result.new_transactions[0] == t_new

            # Persisting the filtered result adds exactly 1 row
            added = store.add_new(new_result)
            assert added == 1

            total = store.conn.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]
            assert total == 3

    def test_rerun_unchanged_input_is_noop(self) -> None:
        """FR-15: a re-run on unchanged input produces zero new rows and no LLM candidates."""
        batch = [
            _txn("SYNTH MERCHANT ONE", amount="-15.00", d=date(2026, 6, 1)),
            _txn("SYNTH MERCHANT TWO", amount="-30.00", d=date(2026, 6, 2)),
            _txn("SYNTH MERCHANT THREE", amount="200.00", bank=Bank.WESTPAC),
        ]

        with Store(":memory:") as store:
            # First ingest — all are new
            first = filter_new_transactions(batch, set())
            store.add_new(first)

            # Second ingest — seen fingerprints supplied from the store
            seen = store.seen_transaction_fingerprints()
            second = filter_new_transactions(batch, seen)

            assert second.new_transactions == ()
            added = store.add_new(second)
            assert added == 0

            # Row count unchanged
            count = store.conn.execute(
                "SELECT COUNT(*) FROM transactions"
            ).fetchone()[0]
            assert count == len(batch)

    def test_empty_db_returns_empty_seen_set(self) -> None:
        """seen_transaction_fingerprints() returns an empty set on an empty database."""
        with Store(":memory:") as store:
            seen = store.seen_transaction_fingerprints()
        assert seen == set()
        assert isinstance(seen, set)


# ---------------------------------------------------------------------------
# TestCategoryPersistence (FR-14)
# ---------------------------------------------------------------------------


class TestCategoryPersistence:
    def test_categories_persist_and_read_back(self) -> None:
        """Insert uncategorised rows; set_categories writes; uncategorised() shrinks;
        categorised_fingerprints gains the fp."""
        t1 = _txn("SYNTH SUPERMARKET", amount="-55.00", d=date(2026, 6, 10))
        t2 = _txn("SYNTH BOOKSTORE", amount="-22.00", d=date(2026, 6, 11))
        r = _result(t1, t2)

        with Store(":memory:") as store:
            store.add_new(r)

            uncats = store.uncategorised()
            assert len(uncats) == 2

            # Verify return type and field types
            for row in uncats:
                assert isinstance(row, UncategorisedRow)
                assert isinstance(row.amount, Decimal)
                assert isinstance(row.id, int)
                assert isinstance(row.txn_fingerprint, str)

            fp1 = r.fingerprints[0]
            updated = store.set_categories({fp1: "Groceries"})
            assert updated == 1

            # uncategorised() shrinks by exactly 1
            remaining = store.uncategorised()
            assert len(remaining) == 1
            assert remaining[0].txn_fingerprint == r.fingerprints[1]

            # categorised_fingerprints() contains fp1 only
            cat_fps = store.categorised_fingerprints()
            assert fp1 in cat_fps
            assert r.fingerprints[1] not in cat_fps

    def test_set_categories_unknown_label_coerced_to_other(self) -> None:
        """An unrecognised category label is stored as 'Other', never raises."""
        t = _txn("SYNTH MYSTERY SHOP", amount="-7.50", d=date(2026, 6, 12))
        r = _result(t)
        fp = r.fingerprints[0]

        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({fp: "Bogus"})

            row = store.conn.execute(
                "SELECT category FROM transactions WHERE txn_fingerprint = ?", (fp,)
            ).fetchone()

        assert row["category"] == "Other"

    def test_set_categories_none_label_coerced_to_other(self) -> None:
        """None category label is coerced to 'Other', never raises."""
        t = _txn("SYNTH NULL CAT SHOP", amount="-3.00", d=date(2026, 6, 13))
        r = _result(t)
        fp = r.fingerprints[0]

        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({fp: None})

            row = store.conn.execute(
                "SELECT category FROM transactions WHERE txn_fingerprint = ?", (fp,)
            ).fetchone()

        assert row["category"] == "Other"

    def test_set_categories_by_id_and_by_fingerprint(self) -> None:
        """str (fingerprint) and int (id) key types both update correctly in one call."""
        t1 = _txn("SYNTH SHOP ALPHA", amount="-10.00", d=date(2026, 6, 14))
        t2 = _txn("SYNTH SHOP BETA", amount="-20.00", d=date(2026, 6, 14))
        r = _result(t1, t2)

        with Store(":memory:") as store:
            store.add_new(r)

            uncats = store.uncategorised()
            assert len(uncats) == 2

            # Use fingerprint (str) for first row, row id (int) for second
            fp1 = uncats[0].txn_fingerprint
            id2 = uncats[1].id

            # Mixed key types in a single mapping
            updated = store.set_categories({fp1: "Transport", id2: "Dining Out"})
            assert updated == 2

            remaining = store.uncategorised()
            assert len(remaining) == 0

            rows = store.conn.execute(
                "SELECT txn_fingerprint, category FROM transactions ORDER BY id"
            ).fetchall()
            assert rows[0]["category"] == "Transport"
            assert rows[1]["category"] == "Dining Out"

    def test_set_categories_missing_key_contributes_zero(self) -> None:
        """A key that does not exist in the table contributes 0 to the updated count."""
        with Store(":memory:") as store:
            updated = store.set_categories({"nonexistent-fp-synthetic-xyz": "Groceries"})
        assert updated == 0

    def test_set_categories_only_taxonomy_labels_stored(self) -> None:
        """After set_categories with various inputs, only taxonomy-valid labels appear in the DB."""
        labels_in = ["Groceries", "Bogus", "Utilities", "", "Income", "WRONG"]
        txns = [_txn(f"SYNTH VENDOR {i}", amount=f"-{(i+1)*5}.00", d=date(2026, 6, i + 1))
                for i in range(len(labels_in))]
        r = _result(*txns)
        fp_to_label = {r.fingerprints[i]: labels_in[i] for i in range(len(labels_in))}

        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories(fp_to_label)

            stored = [
                row[0]
                for row in store.conn.execute(
                    "SELECT category FROM transactions ORDER BY id"
                ).fetchall()
            ]

        taxonomy_set = set(TAXONOMY)
        for cat in stored:
            assert cat is None or cat in taxonomy_set, (
                f"Non-taxonomy value '{cat}' must never be written to the database"
            )


# ---------------------------------------------------------------------------
# TestSummary
# ---------------------------------------------------------------------------


class TestSummaryCorrectness:
    def test_summary_totals_correct(self) -> None:
        """Per-category totals and net are exact Decimal sums serialised as str.
        NULL-category rows appear under the literal key 'Uncategorised'.
        """
        t_g1 = _txn("SYNTH GROCER ONE", amount="-15.00", d=date(2026, 6, 5))
        t_g2 = _txn("SYNTH GROCER TWO", amount="-30.00", d=date(2026, 6, 6))
        t_null = _txn("SYNTH UNKNOWN VENDOR", amount="-5.00", d=date(2026, 6, 7))
        r = _result(t_g1, t_g2, t_null)

        with Store(":memory:") as store:
            store.add_new(r)
            # Categorise t_g1 and t_g2; leave t_null as NULL (-> "Uncategorised")
            store.set_categories({
                r.fingerprints[0]: "Groceries",
                r.fingerprints[1]: "Groceries",
            })
            result = store.summary("2026-06")

        assert result["year_month"] == "2026-06"
        assert result["count"] == 3
        assert result["totals"]["Groceries"] == "-45.00"
        assert result["totals"]["Uncategorised"] == "-5.00"
        assert result["net"] == "-50.00"
        # Money values are strings, not floats
        assert isinstance(result["totals"]["Groceries"], str)
        assert isinstance(result["net"], str)

    def test_summary_defaults_to_latest_month(self) -> None:
        """summary(None) targets MAX(year_month) when multiple months are present."""
        t_may = _txn("SYNTH MAY VENDOR", amount="-100.00", d=date(2026, 5, 15))
        t_jun = _txn("SYNTH JUN VENDOR", amount="-200.00", d=date(2026, 6, 15))
        r = _result(t_may, t_jun)

        with Store(":memory:") as store:
            store.add_new(r)
            result = store.summary(None)

        # June is the latest month; May row must not appear
        assert result["year_month"] == "2026-06"
        assert result["count"] == 1
        assert result["net"] == "-200.00"

    def test_summary_empty_db(self) -> None:
        """Empty database returns the safe empty shape (net '0.00', totals {}, count 0)."""
        with Store(":memory:") as store:
            result = store.summary(None)

        assert result == {
            "year_month": None,
            "totals": {},
            "net": "0.00",
            "count": 0,
            "fuel_rule_applied": False,
            "fuel_rule_eligible": 0,
            "fuel_rule_eligible_amount": "0.00",
            "account_balances": {},
        }

    def test_summary_mixed_income_and_expense(self) -> None:
        """net is the signed arithmetic sum; income offsets expenses exactly."""
        t_expense = _txn("SYNTH ELECTRICITY BILL", amount="-200.00", d=date(2026, 6, 1))
        t_income = _txn("SYNTH SALARY CREDIT", amount="2000.00", d=date(2026, 6, 2))
        r = _result(t_expense, t_income)

        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Housing",
                r.fingerprints[1]: "Income",
            })
            result = store.summary("2026-06")

        assert result["totals"]["Housing"] == "-200.00"
        assert result["totals"]["Income"] == "2000.00"
        assert result["net"] == "1800.00"
        assert result["count"] == 2

    def test_summary_explicit_year_month_filters_correctly(self) -> None:
        """summary with an explicit year_month only returns rows for that month."""
        t_may = _txn("SYNTH MAY ITEM", amount="-50.00", d=date(2026, 5, 10))
        t_jun = _txn("SYNTH JUN ITEM", amount="-75.00", d=date(2026, 6, 10))
        r = _result(t_may, t_jun)

        with Store(":memory:") as store:
            store.add_new(r)

            may_result = store.summary("2026-05")
            jun_result = store.summary("2026-06")

        assert may_result["year_month"] == "2026-05"
        assert may_result["count"] == 1
        assert may_result["net"] == "-50.00"

        assert jun_result["year_month"] == "2026-06"
        assert jun_result["count"] == 1
        assert jun_result["net"] == "-75.00"

    def test_summary_fuel_rule_eligible_counts_under_10_fuel(self) -> None:
        """fuel_rule_eligible counts only under-$10 fuel/convenience Transport rows.

        A fuel token under $10 is eligible; a fuel token over $10 is not; a
        non-fuel/travel token under $10 is not.
        """
        t_small_fuel = _txn("SYNTH BP STOP", amount="-8.00", d=date(2026, 6, 1))
        t_big_fuel = _txn("SYNTH BP FILLUP", amount="-65.00", d=date(2026, 6, 2))
        t_small_travel = _txn("SYNTH OPAL TRAVEL", amount="-3.00", d=date(2026, 6, 3))
        r = _result(t_small_fuel, t_big_fuel, t_small_travel)

        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({fp: "Transport" for fp in r.fingerprints})
            result = store.summary("2026-06")

        assert result["fuel_rule_eligible"] == 1
        assert result["fuel_rule_eligible_amount"] == "-8.00"

    def test_summary_fuel_rule_eligible_stable_after_apply(self) -> None:
        """fuel_rule_eligible is unchanged whether the rule has been applied or not."""
        t_small_fuel = _txn("SYNTH BP STOP", amount="-8.00", d=date(2026, 6, 1))
        t_big_fuel = _txn("SYNTH BP FILLUP", amount="-65.00", d=date(2026, 6, 2))
        t_small_travel = _txn("SYNTH OPAL TRAVEL", amount="-3.00", d=date(2026, 6, 3))
        r = _result(t_small_fuel, t_big_fuel, t_small_travel)

        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({fp: "Transport" for fp in r.fingerprints})
            before = store.summary("2026-06")

            store.apply_fuel_dining_rule("2026-06")
            after = store.summary("2026-06")

        assert before["fuel_rule_eligible"] == after["fuel_rule_eligible"] == 1
        assert before["fuel_rule_eligible_amount"] == after["fuel_rule_eligible_amount"] == "-8.00"
        assert after["fuel_rule_applied"] is True

    def test_summary_fuel_rule_eligible_zero_when_none(self) -> None:
        """A month with no eligible rows reports 0 count and a '0.00' amount."""
        t_big_fuel = _txn("SYNTH BP FILLUP", amount="-65.00", d=date(2026, 6, 1))
        t_grocery = _txn("SYNTH GROCER", amount="-20.00", d=date(2026, 6, 2))
        r = _result(t_big_fuel, t_grocery)

        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Transport",
                r.fingerprints[1]: "Groceries",
            })
            result = store.summary("2026-06")

        assert result["fuel_rule_eligible"] == 0
        assert result["fuel_rule_eligible_amount"] == "0.00"


# ---------------------------------------------------------------------------
# TestTransactionsForMonth
# ---------------------------------------------------------------------------


class TestTransactionsForMonth:
    def test_transactions_for_month_shape_and_ordering(self) -> None:
        """Returns MonthRow rows ordered by date; amounts are Decimal; category None until set."""
        # Insert in non-date order to verify ordering
        t1 = _txn("SYNTH SHOP ALPHA", amount="-10.00", d=date(2026, 6, 3))
        t2 = _txn("SYNTH SHOP BETA", amount="-20.00", d=date(2026, 6, 1))
        t3 = _txn("SYNTH SHOP GAMMA", amount="-30.00", d=date(2026, 6, 2))
        r = _result(t1, t2, t3)

        with Store(":memory:") as store:
            store.add_new(r)
            rows = store.transactions_for_month("2026-06")

        assert len(rows) == 3
        # Ordered by date ascending
        assert [row.date for row in rows] == ["2026-06-01", "2026-06-02", "2026-06-03"]

        for row in rows:
            assert isinstance(row, MonthRow)
            assert isinstance(row.amount, Decimal)
            assert row.category is None

    def test_transactions_for_month_category_populated(self) -> None:
        """Category appears in MonthRow after set_categories."""
        t = _txn("SYNTH MARKET", amount="-35.00", d=date(2026, 6, 5))
        r = _result(t)

        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({r.fingerprints[0]: "Groceries"})
            rows = store.transactions_for_month("2026-06")

        assert len(rows) == 1
        assert rows[0].category == "Groceries"
        assert rows[0].amount == Decimal("-35.00")
        assert rows[0].date == "2026-06-05"
        assert rows[0].description == "SYNTH MARKET"

    def test_transactions_for_month_empty_db_returns_empty_list(self) -> None:
        """Empty database returns []."""
        with Store(":memory:") as store:
            rows = store.transactions_for_month(None)
        assert rows == []

    def test_transactions_for_month_defaults_to_latest(self) -> None:
        """transactions_for_month(None) targets the latest year_month."""
        t_may = _txn("SYNTH MAY ITEM", amount="-10.00", d=date(2026, 5, 20))
        t_jun = _txn("SYNTH JUN ITEM", amount="-20.00", d=date(2026, 6, 20))
        r = _result(t_may, t_jun)

        with Store(":memory:") as store:
            store.add_new(r)
            rows = store.transactions_for_month(None)

        assert len(rows) == 1
        assert rows[0].date == "2026-06-20"


# ---------------------------------------------------------------------------
# TestLayer1FileFingerprint (FR-12)
# ---------------------------------------------------------------------------


class TestLayer1FileFingerprint:
    def test_file_fingerprint_layer1_skip(self) -> None:
        """is_file_processed: False before mark_file_processed, True after (FR-12).
        Second mark_file_processed with same fp must not error and adds no extra row."""
        fp = "synthetic-file-fingerprint-hex-abc123"

        with Store(":memory:") as store:
            assert store.is_file_processed(fp) is False

            store.mark_file_processed(fp)
            assert store.is_file_processed(fp) is True

            # Second call: INSERT OR IGNORE — must not raise, must not add a row
            store.mark_file_processed(fp)
            assert store.is_file_processed(fp) is True

            row_count = store.conn.execute(
                "SELECT COUNT(*) FROM file_fingerprints"
            ).fetchone()[0]
            assert row_count == 1

    def test_different_file_fingerprints_tracked_independently(self) -> None:
        """Two distinct file fingerprints are tracked independently."""
        fp_a = "synthetic-fp-aaaa-file-one"
        fp_b = "synthetic-fp-bbbb-file-two"

        with Store(":memory:") as store:
            store.mark_file_processed(fp_a)
            assert store.is_file_processed(fp_a) is True
            assert store.is_file_processed(fp_b) is False

            store.mark_file_processed(fp_b)
            assert store.is_file_processed(fp_b) is True

            count = store.conn.execute(
                "SELECT COUNT(*) FROM file_fingerprints"
            ).fetchone()[0]
            assert count == 2

    def test_unregistered_fingerprint_not_processed(self) -> None:
        """is_file_processed returns False for an fp never passed to mark_file_processed."""
        with Store(":memory:") as store:
            assert store.is_file_processed("never-seen-fp-synthetic") is False


# ---------------------------------------------------------------------------
# TestAmountHelpers
# ---------------------------------------------------------------------------


class TestAmountHelpers:
    def test_amount_to_text_adds_trailing_zero(self) -> None:
        assert amount_to_text(Decimal("5.5")) == "5.50"

    def test_amount_to_text_negative_zero_folded_to_positive(self) -> None:
        assert amount_to_text(Decimal("-0.00")) == "0.00"

    def test_amount_roundtrip_from_text(self) -> None:
        """amount_from_text(amount_to_text(x)) recovers the quantized Decimal exactly."""
        x = Decimal("5.5")
        assert amount_from_text(amount_to_text(x)) == Decimal("5.50")

    def test_amount_to_text_preserves_exact_value(self) -> None:
        assert amount_to_text(Decimal("-123.45")) == "-123.45"

    def test_amount_to_text_rounds_half_up(self) -> None:
        """Quantize with ROUND_HALF_UP: 1.125 rounds to 1.13."""
        assert amount_to_text(Decimal("1.125")) == "1.13"

    def test_amount_from_text_exact_decimal(self) -> None:
        assert amount_from_text("-25.50") == Decimal("-25.50")

    def test_amount_to_text_large_negative(self) -> None:
        assert amount_to_text(Decimal("-1234.56")) == "-1234.56"

    def test_amount_to_text_zero(self) -> None:
        assert amount_to_text(Decimal("0")) == "0.00"


# ---------------------------------------------------------------------------
# TestImportCreatesNoFiles
# ---------------------------------------------------------------------------


class TestImportCreatesNoFiles:
    def test_resolve_db_path_creates_no_filesystem_artifacts(
        self, tmp_path, monkeypatch
    ) -> None:
        """resolve_db_path() returns a string and creates no file or directory.

        Uses monkeypatch.chdir so that './data/' would resolve inside tmp_path
        (which is clean), then verifies the directory is still absent after the call.
        """
        monkeypatch.delenv("SQLITE_PATH", raising=False)
        monkeypatch.chdir(tmp_path)

        path_str = resolve_db_path()

        assert isinstance(path_str, str)
        # No directory or file should have been created
        assert not (tmp_path / "data").exists(), (
            "resolve_db_path() must NOT create ./data/ — path resolution only"
        )

    def test_store_memory_path_creates_no_disk_files(
        self, tmp_path, monkeypatch
    ) -> None:
        """Store(':memory:') must not create any real file path."""
        monkeypatch.delenv("SQLITE_PATH", raising=False)
        with Store(":memory:") as store:
            # Usable without creating any disk file
            store.conn.execute("SELECT 1 FROM transactions LIMIT 1")

        # Clean tmp_path confirms nothing was written alongside this test
        assert not (tmp_path / "data").exists()


# ---------------------------------------------------------------------------
# TestLatestYearMonth
# ---------------------------------------------------------------------------


class TestLatestYearMonth:
    def test_latest_year_month_empty_db_returns_none(self) -> None:
        with Store(":memory:") as store:
            assert store.latest_year_month() is None

    def test_latest_year_month_single_month(self) -> None:
        t = _txn("SYNTH VENDOR", amount="-10.00", d=date(2026, 6, 15))
        with Store(":memory:") as store:
            store.add_new(_result(t))
            assert store.latest_year_month() == "2026-06"

    def test_latest_year_month_multiple_months_returns_max(self) -> None:
        t1 = _txn("SYNTH VENDOR A", amount="-10.00", d=date(2026, 4, 1))
        t2 = _txn("SYNTH VENDOR B", amount="-20.00", d=date(2026, 5, 1))
        t3 = _txn("SYNTH VENDOR C", amount="-30.00", d=date(2026, 6, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t1, t2, t3))
            assert store.latest_year_month() == "2026-06"


# ---------------------------------------------------------------------------
# TestTaxonomy
# ---------------------------------------------------------------------------


class TestTaxonomy:
    def test_all_taxonomy_labels_accepted_unchanged(self) -> None:
        """Every label in TAXONOMY passes through coerce_category unchanged."""
        for label in TAXONOMY:
            assert coerce_category(label) == label

    def test_unknown_label_coerced_to_other(self) -> None:
        assert coerce_category("NotACategory") == "Other"

    def test_none_coerced_to_other(self) -> None:
        assert coerce_category(None) == "Other"

    def test_empty_string_coerced_to_other(self) -> None:
        assert coerce_category("") == "Other"

    def test_case_sensitive_mismatch_coerced_to_other(self) -> None:
        """Coercion is exact-match on canonical strings; lowercase fails."""
        assert coerce_category("groceries") == "Other"
        assert coerce_category("GROCERIES") == "Other"
        assert coerce_category("dining out") == "Other"


# ---------------------------------------------------------------------------
# T3 — Store roundtrip (balance)
# ---------------------------------------------------------------------------


class TestBalanceRoundtrip:
    """T3: add_new persists balance; a None balance is stored as NULL."""

    def test_balance_persisted_and_read_back(self) -> None:
        t = _txn("SYNTH RETAILER", amount="-45.99", d=date(2026, 6, 15), balance="954.01")
        r = _result(t)
        with Store(":memory:") as store:
            store.add_new(r)
            row = store.conn.execute(
                "SELECT balance FROM transactions WHERE txn_fingerprint = ?",
                (r.fingerprints[0],),
            ).fetchone()
        assert row["balance"] == "954.01"

    def test_none_balance_stored_as_null(self) -> None:
        t = _txn("SYNTH RETAILER TWO", amount="-10.00", d=date(2026, 6, 16))
        r = _result(t)
        with Store(":memory:") as store:
            store.add_new(r)
            row = store.conn.execute(
                "SELECT balance FROM transactions WHERE txn_fingerprint = ?",
                (r.fingerprints[0],),
            ).fetchone()
        assert row["balance"] is None


# ---------------------------------------------------------------------------
# T4a/T4b — Idempotency unchanged by balance
# ---------------------------------------------------------------------------


class TestBalanceIdempotency:
    def test_identical_balance_rerun_writes_nothing(self) -> None:
        """T4a: add_new twice with identical fingerprint AND identical balance -> 0 writes."""
        t = _txn("SYNTH SHOP", amount="-20.00", d=date(2026, 6, 1), balance="480.00")
        r = _result(t)
        with Store(":memory:") as store:
            first = store.add_new(r)
            second = store.add_new(r)
            count = store.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert first == 1
        assert second == 0, "identical-balance re-run must write nothing (upsert WHERE is false)"
        assert count == 1

    def test_fingerprint_excludes_balance(self) -> None:
        """T4b: two Transactions identical except balance produce the SAME fingerprint."""
        t1 = _txn("SYNTH SHOP", amount="-20.00", d=date(2026, 6, 1), balance="480.00")
        t2 = _txn("SYNTH SHOP", amount="-20.00", d=date(2026, 6, 1), balance="999.99")
        assert transaction_fingerprint(t1) == transaction_fingerprint(t2)


# ---------------------------------------------------------------------------
# add_new's OWN upsert conflict branch (not via reconcile_balances) — the
# "ON CONFLICT ... DO UPDATE SET balance = excluded.balance" WHERE clause
# flagged by the tester-focus notes as the crux of correctness+idempotency.
# ---------------------------------------------------------------------------


class TestAddNewUpsertConflictBranch:
    def test_differing_balance_conflict_updates_balance_no_duplicate_no_category_change(
        self,
    ) -> None:
        """Calling add_new a SECOND time with the same fingerprint but a DIFFERENT,
        non-null balance hits the upsert's ON CONFLICT branch directly: balance is
        updated in place, no duplicate row is created, and category (set between the
        two add_new calls) is left untouched — add_new's SET clause never touches it."""
        t_v1 = _txn("SYNTH SHOP", amount="-20.00", d=date(2026, 6, 1), balance="480.00")
        r_v1 = _result(t_v1)

        with Store(":memory:") as store:
            first = store.add_new(r_v1)
            store.set_categories({r_v1.fingerprints[0]: "Groceries"})

            # Same transaction (same fingerprint), corrected balance.
            t_v2 = _txn("SYNTH SHOP", amount="-20.00", d=date(2026, 6, 1), balance="475.00")
            r_v2 = _result(t_v2)
            second = store.add_new(r_v2)

            row_count = store.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            row = store.conn.execute(
                "SELECT balance, category FROM transactions WHERE txn_fingerprint = ?",
                (r_v1.fingerprints[0],),
            ).fetchone()

        assert first == 1
        assert second == 1, "a differing-balance conflict must report exactly 1 change"
        assert row_count == 1, "no duplicate row on fingerprint conflict"
        assert row["balance"] == "475.00"
        assert row["category"] == "Groceries", "add_new's upsert must never touch category"

    def test_stored_balance_null_new_non_null_conflict_fills_it_in(self) -> None:
        """Stored balance NULL + a new non-null balance on conflict -> filled in (the
        WHERE clause's `transactions.balance IS NULL` arm)."""
        t_no_balance = _txn("SYNTH SHOP", amount="-20.00", d=date(2026, 6, 1))  # balance=None
        r1 = _result(t_no_balance)

        with Store(":memory:") as store:
            store.add_new(r1)

            t_with_balance = _txn(
                "SYNTH SHOP", amount="-20.00", d=date(2026, 6, 1), balance="480.00"
            )
            r2 = _result(t_with_balance)
            second = store.add_new(r2)

            row = store.conn.execute(
                "SELECT balance FROM transactions WHERE txn_fingerprint = ?",
                (r1.fingerprints[0],),
            ).fetchone()

        assert second == 1
        assert row["balance"] == "480.00"

    def test_conflict_with_null_new_balance_never_wipes_stored_balance(self) -> None:
        """A conflicting row whose NEW balance is None must NOT wipe an existing stored
        balance (WHERE excluded.balance IS NOT NULL guards this)."""
        t_with_balance = _txn(
            "SYNTH SHOP", amount="-20.00", d=date(2026, 6, 1), balance="480.00"
        )
        r1 = _result(t_with_balance)

        with Store(":memory:") as store:
            store.add_new(r1)

            t_no_balance = _txn("SYNTH SHOP", amount="-20.00", d=date(2026, 6, 1))  # None
            r2 = _result(t_no_balance)
            second = store.add_new(r2)

            row = store.conn.execute(
                "SELECT balance FROM transactions WHERE txn_fingerprint = ?",
                (r1.fingerprints[0],),
            ).fetchone()

        assert second == 0, "a null new balance must not count as a change"
        assert row["balance"] == "480.00", "stored balance must survive a null-balance conflict"


# ---------------------------------------------------------------------------
# T5 — Order-agnostic opening/closing derivation
# ---------------------------------------------------------------------------


class TestAccountBalanceDerivation:
    def test_oldest_first_insertion_derives_correct_balances(self) -> None:
        """T5a: rows inserted oldest-first -> correct opening/closing."""
        t1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        t2 = _txn("SYNTH SHOP B", amount="-20.00", d=date(2026, 6, 2), balance="70.00")
        t3 = _txn("SYNTH SHOP C", amount="5.00", d=date(2026, 6, 3), balance="75.00")
        with Store(":memory:") as store:
            store.add_new(_result(t1, t2, t3))
            balances = store.account_balances("2026-06")
        assert balances["commbank"] == {"opening": "100.00", "closing": "75.00"}

    def test_newest_first_insertion_identical_result(self) -> None:
        """T5b: same chain inserted newest-first -> IDENTICAL opening/closing (order-agnostic)."""
        t1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        t2 = _txn("SYNTH SHOP B", amount="-20.00", d=date(2026, 6, 2), balance="70.00")
        t3 = _txn("SYNTH SHOP C", amount="5.00", d=date(2026, 6, 3), balance="75.00")
        with Store(":memory:") as store:
            # Insertion (id ascending) order is t3, t2, t1 — the reverse of chronological.
            store.add_new(_result(t3, t2, t1))
            balances = store.account_balances("2026-06")
        assert balances["commbank"] == {"opening": "100.00", "closing": "75.00"}

    def test_single_row_month_derivable(self) -> None:
        """T5c: single-row month derives directly (opening = balance - amount)."""
        t = _txn("SYNTH SHOP SOLO", amount="-15.00", d=date(2026, 6, 5), balance="85.00")
        with Store(":memory:") as store:
            store.add_new(_result(t))
            balances = store.account_balances("2026-06")
        assert balances["commbank"] == {"opening": "100.00", "closing": "85.00"}

    def test_undetermined_null_balance_mid_sequence(self) -> None:
        """T5d: a null balance mid-sequence -> unavailable fallback."""
        t1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        t2 = _txn("SYNTH SHOP B", amount="-20.00", d=date(2026, 6, 2))  # balance missing
        t3 = _txn("SYNTH SHOP C", amount="5.00", d=date(2026, 6, 3), balance="75.00")
        with Store(":memory:") as store:
            store.add_new(_result(t1, t2, t3))
            balances = store.account_balances("2026-06")
        assert balances["commbank"] == {"opening": None, "closing": None}

    def test_undetermined_inconsistent_chain(self) -> None:
        """T5d: a chain matching neither direction -> unavailable fallback, never a wrong number."""
        t1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        t2 = _txn("SYNTH SHOP B", amount="-20.00", d=date(2026, 6, 2), balance="12345.00")
        t3 = _txn("SYNTH SHOP C", amount="5.00", d=date(2026, 6, 3), balance="75.00")
        with Store(":memory:") as store:
            store.add_new(_result(t1, t2, t3))
            balances = store.account_balances("2026-06")
        assert balances["commbank"] == {"opening": None, "closing": None}

    def test_undetermined_both_directions_valid_but_disagree(self) -> None:
        """Both asc and desc chains are internally self-consistent (each passes its own
        running-balance check) but yield DIFFERENT (opening, closing) pairs -> unavailable.

        This exercises the asc_result != desc_result branch of _derive_account_balance,
        distinct from T5d's 'neither direction valid' case. Amounts are symmetric
        (-10 / +10) so BOTH the forward and reverse chains pass the tolerance check,
        yet imply different opening balances (110 vs 100) — genuinely ambiguous.
        """
        t1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="100.00")
        t2 = _txn("SYNTH SHOP B", amount="10.00", d=date(2026, 6, 2), balance="110.00")
        with Store(":memory:") as store:
            store.add_new(_result(t1, t2))
            balances = store.account_balances("2026-06")
        assert balances["commbank"] == {"opening": None, "closing": None}

    def test_single_row_missing_balance_unavailable(self) -> None:
        """A single-row month with no balance is unavailable, not a guess."""
        t = _txn("SYNTH SHOP SOLO", amount="-15.00", d=date(2026, 6, 5))
        with Store(":memory:") as store:
            store.add_new(_result(t))
            balances = store.account_balances("2026-06")
        assert balances["commbank"] == {"opening": None, "closing": None}

    def test_no_rows_for_bank_omits_key(self) -> None:
        """A bank with zero rows this month is entirely absent from the dict."""
        t = _txn("SYNTH SHOP", amount="-10.00", d=date(2026, 6, 1), balance="90.00", bank=Bank.COMMBANK)
        with Store(":memory:") as store:
            store.add_new(_result(t))
            balances = store.account_balances("2026-06")
        assert "westpac" not in balances

    def test_empty_db_returns_empty_dict(self) -> None:
        with Store(":memory:") as store:
            balances = store.account_balances(None)
        assert balances == {}


# ---------------------------------------------------------------------------
# T6 — Two-account derivation
# ---------------------------------------------------------------------------


class TestTwoAccountDerivation:
    def test_two_banks_independent_balances_no_combined_key(self) -> None:
        """T6: two independent per-bank keys; no combined/summed figure anywhere."""
        cb1 = _txn("SYNTH CB SHOP", amount="-10.00", d=date(2026, 6, 1), bank=Bank.COMMBANK, balance="90.00")
        cb2 = _txn("SYNTH CB SHOP TWO", amount="-5.00", d=date(2026, 6, 2), bank=Bank.COMMBANK, balance="85.00")
        wp1 = _txn("SYNTH WP SHOP", amount="-50.00", d=date(2026, 6, 1), bank=Bank.WESTPAC, balance="450.00")
        wp2 = _txn("SYNTH WP SHOP TWO", amount="100.00", d=date(2026, 6, 2), bank=Bank.WESTPAC, balance="550.00")

        with Store(":memory:") as store:
            store.add_new(_result(cb1, cb2, wp1, wp2))
            balances = store.account_balances("2026-06")

        assert set(balances.keys()) == {"commbank", "westpac"}
        assert balances["commbank"] == {"opening": "100.00", "closing": "85.00"}
        assert balances["westpac"] == {"opening": "500.00", "closing": "550.00"}
        assert "combined" not in balances
        assert "total" not in balances

    def test_summary_exposes_account_balances_key(self) -> None:
        """summary() surfaces account_balances additively; existing keys unchanged."""
        cb = _txn("SYNTH CB SHOP", amount="-10.00", d=date(2026, 6, 1), bank=Bank.COMMBANK, balance="90.00")
        with Store(":memory:") as store:
            store.add_new(_result(cb))
            result = store.summary("2026-06")
        assert result["account_balances"] == {"commbank": {"opening": "100.00", "closing": "90.00"}}
        # Existing keys still present (additive change only).
        assert "totals" in result and "net" in result and "count" in result


# ---------------------------------------------------------------------------
# T7 (store-level) — privacy: zero network code in the balance code path
# ---------------------------------------------------------------------------


class TestBalancePrivacyStoreLevel:
    def test_store_module_has_no_network_imports(self) -> None:
        """T7 (store-level): store.py — including reconcile_balances/account_balances
        — imports zero network libraries (matches the module's own docstring claim)."""
        import ast
        import inspect

        from backend.store import store as store_module

        tree = ast.parse(inspect.getsource(store_module))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])

        forbidden = {"requests", "httpx", "urllib", "socket", "http", "aiohttp"}
        leaked = imported_roots & forbidden
        assert not leaked, f"store.py must import zero network libraries (found {leaked})"

    def test_reconcile_balances_never_touches_category(self) -> None:
        """Balance-only reconciliation never mutates category, even when one is set."""
        t = _txn("SYNTH SHOP", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        r = _result(t)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({r.fingerprints[0]: "Groceries"})
            corrected = _txn("SYNTH SHOP", amount="-10.00", d=date(2026, 6, 1), balance="88.00")
            store.reconcile_balances([corrected])
            row = store.conn.execute(
                "SELECT category, balance FROM transactions WHERE txn_fingerprint = ?",
                (r.fingerprints[0],),
            ).fetchone()
        assert row["category"] == "Groceries"
        assert row["balance"] == "88.00"


# ---------------------------------------------------------------------------
# T10 — reconcile_balances unit tests
# ---------------------------------------------------------------------------


class TestReconcileBalances:
    def test_balance_correction_updates_in_place(self) -> None:
        t = _txn("SYNTH SHOP", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        r = _result(t)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({r.fingerprints[0]: "Groceries"})

            corrected = _txn("SYNTH SHOP", amount="-10.00", d=date(2026, 6, 1), balance="88.00")
            updated = store.reconcile_balances([corrected])

            row_count = store.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            row = store.conn.execute(
                "SELECT balance, category FROM transactions WHERE txn_fingerprint = ?",
                (r.fingerprints[0],),
            ).fetchone()

        assert updated == 1
        assert row_count == 1, "no duplicate row must be created"
        assert row["balance"] == "88.00"
        assert row["category"] == "Groceries"

    def test_second_identical_call_returns_zero(self) -> None:
        t = _txn("SYNTH SHOP", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        r = _result(t)
        with Store(":memory:") as store:
            store.add_new(r)
            corrected = _txn("SYNTH SHOP", amount="-10.00", d=date(2026, 6, 1), balance="88.00")
            store.reconcile_balances([corrected])
            second = store.reconcile_balances([corrected])
        assert second == 0

    def test_none_balance_keeps_stored_value_returns_zero(self) -> None:
        t = _txn("SYNTH SHOP", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        r = _result(t)
        with Store(":memory:") as store:
            store.add_new(r)
            missing_balance = _txn("SYNTH SHOP", amount="-10.00", d=date(2026, 6, 1))  # balance=None
            updated = store.reconcile_balances([missing_balance])
            row = store.conn.execute(
                "SELECT balance FROM transactions WHERE txn_fingerprint = ?",
                (r.fingerprints[0],),
            ).fetchone()
        assert updated == 0
        assert row["balance"] == "90.00"

    def test_unknown_fingerprint_skipped(self) -> None:
        unseen = _txn("SYNTH SHOP UNSEEN", amount="-5.00", d=date(2026, 6, 1), balance="50.00")
        with Store(":memory:") as store:
            updated = store.reconcile_balances([unseen])
            count = store.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        assert updated == 0
        assert count == 0

    def test_empty_list_returns_zero(self) -> None:
        with Store(":memory:") as store:
            assert store.reconcile_balances([]) == 0


# ---------------------------------------------------------------------------
# TestPeriodViews (v2 Pass 1) — Monthly / Yearly + period-over-period
# comparison. All fixtures are SYNTHETIC, generated inline. No real
# transactions, no real CSVs.
# ---------------------------------------------------------------------------


class TestAvailablePeriods:
    def test_available_months_empty_db_returns_empty_list(self) -> None:
        with Store(":memory:") as store:
            assert store.available_months() == []

    def test_available_years_empty_db_returns_empty_list(self) -> None:
        with Store(":memory:") as store:
            assert store.available_years() == []

    def test_available_months_distinct_descending(self) -> None:
        t1 = _txn("SYNTH A", amount="-10.00", d=date(2026, 4, 1))
        t2 = _txn("SYNTH B", amount="-20.00", d=date(2026, 4, 2))  # same month as t1
        t3 = _txn("SYNTH C", amount="-30.00", d=date(2026, 6, 1))
        t4 = _txn("SYNTH D", amount="-40.00", d=date(2026, 3, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t1, t2, t3, t4))
            assert store.available_months() == ["2026-06", "2026-04", "2026-03"]

    def test_available_years_distinct_descending(self) -> None:
        t1 = _txn("SYNTH A", amount="-10.00", d=date(2025, 3, 1))
        t2 = _txn("SYNTH B", amount="-20.00", d=date(2025, 11, 1))  # same year as t1
        t3 = _txn("SYNTH C", amount="-30.00", d=date(2026, 1, 1))
        t4 = _txn("SYNTH D", amount="-40.00", d=date(2024, 6, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t1, t2, t3, t4))
            assert store.available_years() == ["2026", "2025", "2024"]


class TestMonthView:
    def test_empty_db_exact_shape(self) -> None:
        with Store(":memory:") as store:
            result = store.month_view(None)
        assert result == {
            "period": "month",
            "ym": None,
            "prev_ym": None,
            "totals": {},
            "net": "0.00",
            "count": 0,
            "comparison": [],
            "available_months": [],
        }

    def test_happy_path_totals_net_count_and_str_money(self) -> None:
        """Decimal-exact accumulation; str(Decimal) out; NULL -> 'Uncategorised'."""
        t_g1 = _txn("SYNTH GROCER ONE", amount="-33.33", d=date(2026, 6, 1))
        t_g2 = _txn("SYNTH GROCER TWO", amount="-66.67", d=date(2026, 6, 2))
        t_null = _txn("SYNTH UNKNOWN VENDOR", amount="-5.00", d=date(2026, 6, 3))
        r = _result(t_g1, t_g2, t_null)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Groceries",
                r.fingerprints[1]: "Groceries",
            })
            result = store.month_view("2026-06")

        assert result["period"] == "month"
        assert result["ym"] == "2026-06"
        assert result["count"] == 3
        assert result["totals"]["Groceries"] == "-100.00"
        assert result["totals"]["Uncategorised"] == "-5.00"
        assert result["net"] == "-105.00"
        for v in result["totals"].values():
            assert isinstance(v, str)
        assert isinstance(result["net"], str)

    def test_defaults_to_latest_populated_month(self) -> None:
        t_may = _txn("SYNTH MAY VENDOR", amount="-100.00", d=date(2026, 5, 15))
        t_jun = _txn("SYNTH JUN VENDOR", amount="-200.00", d=date(2026, 6, 15))
        with Store(":memory:") as store:
            store.add_new(_result(t_may, t_jun))
            result = store.month_view(None)
        assert result["ym"] == "2026-06"
        assert result["count"] == 1

    def test_single_period_no_previous_is_graceful(self) -> None:
        """Single populated month: prev_ym is null; comparison still returns with
        previous '0.00' and pct_change null — not an error, not empty."""
        t = _txn("SYNTH ONLY MONTH ITEM", amount="-40.00", d=date(2026, 6, 1))
        r = _result(t)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({r.fingerprints[0]: "Groceries"})
            result = store.month_view("2026-06")

        assert result["prev_ym"] is None
        assert len(result["comparison"]) == 1
        row = result["comparison"][0]
        assert row["category"] == "Groceries"
        assert row["current"] == "-40.00"
        assert row["previous"] == "0.00"
        assert row["pct_change"] is None

    def test_skip_gap_previous_populated_month(self) -> None:
        """Data in 2026-06 and 2026-03 (gap at 04/05) -> prev_ym == '2026-03'."""
        t_mar = _txn("SYNTH MARCH ITEM", amount="-10.00", d=date(2026, 3, 5))
        t_jun = _txn("SYNTH JUNE ITEM", amount="-20.00", d=date(2026, 6, 5))
        with Store(":memory:") as store:
            store.add_new(_result(t_mar, t_jun))
            result = store.month_view("2026-06")
        assert result["prev_ym"] == "2026-03"
        assert result["available_months"] == ["2026-06", "2026-03"]

    def test_comparison_delta_and_pct_change_growth(self) -> None:
        """Category present both periods, spend growing more negative -> positive pct.

        Mirrors the spec's worked example exactly: current -150.00, previous
        -100.00 -> delta -50.00, pct_change 50.0.
        """
        t_prev = _txn("SYNTH GROCER MAY", amount="-100.00", d=date(2026, 5, 10))
        t_cur = _txn("SYNTH GROCER JUN", amount="-150.00", d=date(2026, 6, 10))
        r = _result(t_prev, t_cur)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Groceries",
                r.fingerprints[1]: "Groceries",
            })
            result = store.month_view("2026-06")

        row = next(c for c in result["comparison"] if c["category"] == "Groceries")
        assert row["current"] == "-150.00"
        assert row["previous"] == "-100.00"
        assert row["delta"] == "-50.00"
        assert row["pct_change"] == 50.0

    def test_comparison_brand_new_category_no_previous_pct_none(self) -> None:
        """Category present in current but absent from previous -> previous '0.00',
        pct_change null (never a huge number, never a divide-by-zero crash)."""
        t_prev = _txn("SYNTH RENT MAY", amount="-100.00", d=date(2026, 5, 1))
        t_cur_rent = _txn("SYNTH RENT JUN", amount="-100.00", d=date(2026, 6, 1))
        t_cur_new = _txn("SYNTH NEW SUBSCRIPTION", amount="-15.00", d=date(2026, 6, 2))
        r = _result(t_prev, t_cur_rent, t_cur_new)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Housing",
                r.fingerprints[1]: "Housing",
                r.fingerprints[2]: "Subscriptions",
            })
            result = store.month_view("2026-06")

        row = next(c for c in result["comparison"] if c["category"] == "Subscriptions")
        assert row["current"] == "-15.00"
        assert row["previous"] == "0.00"
        assert row["delta"] == "-15.00"
        assert row["pct_change"] is None

    def test_comparison_category_absent_from_current(self) -> None:
        """Category present only in previous -> current '0.00', sensible delta/pct."""
        t_prev_only = _txn("SYNTH ONE-OFF FEE", amount="-900.00", d=date(2026, 5, 1))
        t_cur = _txn("SYNTH GROCER JUN", amount="-50.00", d=date(2026, 6, 1))
        r = _result(t_prev_only, t_cur)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Housing",
                r.fingerprints[1]: "Groceries",
            })
            result = store.month_view("2026-06")

        row = next(c for c in result["comparison"] if c["category"] == "Housing")
        assert row["current"] == "0.00"
        assert row["previous"] == "-900.00"
        assert row["delta"] == "900.00"
        # magnitude: (|current| - |previous|) / |previous| * 100
        #          = (0 - 900) / 900 * 100 = -100.0
        assert row["pct_change"] == -100.0

    def test_comparison_sign_flip_uses_magnitude(self) -> None:
        """A category that was net-spend and becomes net-refund (positive) reports a
        magnitude-based pct, not a signed-denominator one.

        previous -100.00 (spent), current +50.00 (net refund) -> delta +150.00,
        magnitude pct = (|50| - |100|) / |100| * 100 = -50.0. Under the old signed
        denominator this was (50 - -100) / -100 * 100 = -150.0, so this test locks in
        the magnitude behaviour.
        """
        t_prev = _txn("SYNTH FLIP MAY", amount="-100.00", d=date(2026, 5, 1))
        t_cur = _txn("SYNTH FLIP JUN", amount="50.00", d=date(2026, 6, 1))
        r = _result(t_prev, t_cur)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Groceries",
                r.fingerprints[1]: "Groceries",
            })
            result = store.month_view("2026-06")

        row = next(c for c in result["comparison"] if c["category"] == "Groceries")
        assert row["current"] == "50.00"
        assert row["previous"] == "-100.00"
        assert row["delta"] == "150.00"
        assert row["pct_change"] == -50.0

    def test_comparison_ordered_by_abs_current_desc(self) -> None:
        t1 = _txn("SYNTH SMALL", amount="-10.00", d=date(2026, 6, 1))
        t2 = _txn("SYNTH BIG", amount="-500.00", d=date(2026, 6, 2))
        t3 = _txn("SYNTH MEDIUM", amount="-100.00", d=date(2026, 6, 3))
        r = _result(t1, t2, t3)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Other",
                r.fingerprints[1]: "Housing",
                r.fingerprints[2]: "Groceries",
            })
            result = store.month_view("2026-06")

        names = [row["category"] for row in result["comparison"]]
        assert names == ["Housing", "Groceries", "Other"]

    def test_available_months_included_in_response(self) -> None:
        t1 = _txn("SYNTH A", amount="-10.00", d=date(2026, 5, 1))
        t2 = _txn("SYNTH B", amount="-20.00", d=date(2026, 6, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t1, t2))
            result = store.month_view("2026-06")
        assert result["available_months"] == ["2026-06", "2026-05"]


class TestYearView:
    def test_empty_db_exact_shape(self) -> None:
        with Store(":memory:") as store:
            result = store.year_view(None)
        assert result == {
            "period": "year",
            "y": None,
            "prev_y": None,
            "totals": {},
            "net": "0.00",
            "count": 0,
            "comparison": [],
            "available_years": [],
        }

    def test_happy_path_aggregates_across_months_in_one_year(self) -> None:
        t1 = _txn("SYNTH JAN ITEM", amount="-40.00", d=date(2026, 1, 5))
        t2 = _txn("SYNTH JUN ITEM", amount="-60.00", d=date(2026, 6, 5))
        t_null = _txn("SYNTH UNKNOWN ITEM", amount="-5.00", d=date(2026, 3, 5))
        r = _result(t1, t2, t_null)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Groceries",
                r.fingerprints[1]: "Groceries",
            })
            result = store.year_view("2026")

        assert result["period"] == "year"
        assert result["y"] == "2026"
        assert result["count"] == 3
        assert result["totals"]["Groceries"] == "-100.00"
        assert result["totals"]["Uncategorised"] == "-5.00"
        assert result["net"] == "-105.00"
        for v in result["totals"].values():
            assert isinstance(v, str)

    def test_defaults_to_latest_populated_year(self) -> None:
        t_2025 = _txn("SYNTH 2025 ITEM", amount="-100.00", d=date(2025, 6, 1))
        t_2026 = _txn("SYNTH 2026 ITEM", amount="-200.00", d=date(2026, 1, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t_2025, t_2026))
            result = store.year_view(None)
        assert result["y"] == "2026"
        assert result["count"] == 1

    def test_single_period_no_previous_is_graceful(self) -> None:
        t = _txn("SYNTH ONLY YEAR ITEM", amount="-70.00", d=date(2026, 1, 1))
        r = _result(t)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({r.fingerprints[0]: "Groceries"})
            result = store.year_view("2026")

        assert result["prev_y"] is None
        assert len(result["comparison"]) == 1
        row = result["comparison"][0]
        assert row["current"] == "-70.00"
        assert row["previous"] == "0.00"
        assert row["pct_change"] is None

    def test_skip_gap_previous_populated_year(self) -> None:
        """Data in 2026 and 2024 (gap at 2025) -> prev_y == '2024'."""
        t_2024 = _txn("SYNTH 2024 ITEM", amount="-10.00", d=date(2024, 5, 1))
        t_2026 = _txn("SYNTH 2026 ITEM", amount="-20.00", d=date(2026, 5, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t_2024, t_2026))
            result = store.year_view("2026")
        assert result["prev_y"] == "2024"
        assert result["available_years"] == ["2026", "2024"]

    def test_comparison_delta_and_pct_change(self) -> None:
        t_prev = _txn("SYNTH GROCER 2025", amount="-1000.00", d=date(2025, 6, 1))
        t_cur = _txn("SYNTH GROCER 2026", amount="-1500.00", d=date(2026, 6, 1))
        r = _result(t_prev, t_cur)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Groceries",
                r.fingerprints[1]: "Groceries",
            })
            result = store.year_view("2026")

        row = next(c for c in result["comparison"] if c["category"] == "Groceries")
        assert row["current"] == "-1500.00"
        assert row["previous"] == "-1000.00"
        assert row["delta"] == "-500.00"
        assert row["pct_change"] == 50.0

    def test_available_years_included_in_response(self) -> None:
        t1 = _txn("SYNTH A", amount="-10.00", d=date(2025, 1, 1))
        t2 = _txn("SYNTH B", amount="-20.00", d=date(2026, 1, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t1, t2))
            result = store.year_view("2026")
        assert result["available_years"] == ["2026", "2025"]


# ---------------------------------------------------------------------------
# TestMonthRangeHelper — _month_range (v2 Pass 2). Pure, no IO.
# ---------------------------------------------------------------------------


class TestMonthRangeHelper:
    def test_ascending_order_basic(self) -> None:
        assert _month_range("2026-06", 4) == ["2026-03", "2026-04", "2026-05", "2026-06"]

    def test_single_month_returns_end_only(self) -> None:
        assert _month_range("2026-06", 1) == ["2026-06"]

    def test_year_boundary_dec_to_jan(self) -> None:
        """Dec -> Jan crossing must decrement the year, not wrap within the same year."""
        assert _month_range("2026-01", 3) == ["2025-11", "2025-12", "2026-01"]

    def test_full_calendar_year_plus_one_wraps_twice(self) -> None:
        result = _month_range("2026-01", 13)
        assert result[0] == "2025-01"
        assert result[-1] == "2026-01"
        assert len(result) == 13
        # Strictly ascending, no duplicates
        assert result == sorted(result)
        assert len(set(result)) == 13


# ---------------------------------------------------------------------------
# TestCategoryTrend — Store.category_trend (v2 Pass 2). All fixtures are
# SYNTHETIC, generated inline. No real transactions, no real CSVs.
# ---------------------------------------------------------------------------


class TestCategoryTrend:
    # -- empty DB --------------------------------------------------------

    def test_empty_db_exact_shape(self) -> None:
        with Store(":memory:") as store:
            result = store.category_trend()
        assert result == {
            "window": 6,
            "end_month": None,
            "months": [],
            "series": [],
            "spend_by_month": [],
            "months_available": 0,
        }

    def test_empty_db_window_reflects_clamped_months_arg_not_raise(self) -> None:
        """Failure case: a nonsense months arg against an empty DB returns the
        defined empty shape (clamped window) rather than raising."""
        with Store(":memory:") as store:
            over = store.category_trend(months=100)
            under = store.category_trend(months=0)
            negative = store.category_trend(months=-5)
        assert over["window"] == 24
        assert over["months"] == []
        assert under["window"] == 1
        assert negative["window"] == 1

    # -- happy path --------------------------------------------------------

    def test_happy_path_multi_month_multi_category(self) -> None:
        """3+ categories across 4 consecutive months; ascending months; gap
        months zero-filled; every value is str, never float."""
        txns = [
            _txn("SYNTH GROC MAR", amount="-20.00", d=date(2026, 3, 5)),
            _txn("SYNTH GROC APR", amount="-30.00", d=date(2026, 4, 5)),
            _txn("SYNTH TRANSPORT APR", amount="-10.00", d=date(2026, 4, 6)),
            _txn("SYNTH SALARY MAY", amount="3000.00", d=date(2026, 5, 5)),
            _txn("SYNTH GROC JUN", amount="-15.00", d=date(2026, 6, 5)),
        ]
        r = _result(*txns)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Groceries",
                r.fingerprints[1]: "Groceries",
                r.fingerprints[2]: "Transport",
                r.fingerprints[3]: "Income",
                r.fingerprints[4]: "Groceries",
            })
            result = store.category_trend(months=4, end_month="2026-06")

        assert result["window"] == 4
        assert result["end_month"] == "2026-06"
        assert result["months"] == ["2026-03", "2026-04", "2026-05", "2026-06"]
        assert result["months"] == sorted(result["months"]), "months must be ascending"

        for s in result["series"]:
            assert len(s["values"]) == 4
            for v in s["values"]:
                assert isinstance(v, str), f"{s['category']} value {v!r} must be str, not float"

        groceries = next(s for s in result["series"] if s["category"] == "Groceries")
        assert groceries["values"] == ["-20.00", "-30.00", "0.00", "-15.00"]

        transport = next(s for s in result["series"] if s["category"] == "Transport")
        assert transport["values"] == ["0.00", "-10.00", "0.00", "0.00"]

        income = next(s for s in result["series"] if s["category"] == "Income")
        assert income["values"] == ["0.00", "0.00", "3000.00", "0.00"]

        assert result["months_available"] == 4

    def test_ordering_taxonomy_then_uncategorised_last(self) -> None:
        txns = [
            _txn("SYNTH UNCAT ITEM", amount="-5.00", d=date(2026, 6, 1)),
            _txn("SYNTH SALARY", amount="1000.00", d=date(2026, 6, 2)),
            _txn("SYNTH RENT", amount="-800.00", d=date(2026, 6, 3)),
            _txn("SYNTH GROC", amount="-50.00", d=date(2026, 6, 4)),
        ]
        r = _result(*txns)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[1]: "Income",
                r.fingerprints[2]: "Housing",
                r.fingerprints[3]: "Groceries",
                # r.fingerprints[0] left uncategorised (NULL) -> "Uncategorised"
            })
            result = store.category_trend(months=1, end_month="2026-06")

        names = [s["category"] for s in result["series"]]
        # TAXONOMY order = Groceries, Housing, Dining Out, ... Income, Other
        assert names == ["Groceries", "Housing", "Income", "Uncategorised"]
        assert names.index("Uncategorised") == len(names) - 1

    def test_spend_by_month_excludes_income_and_net_positive_categories(self) -> None:
        """spend_by_month = donut spend magnitude: excludes Income entirely and
        excludes any category whose monthly total is net-positive (e.g. a refund)."""
        txns = [
            _txn("SYNTH GROC", amount="-100.00", d=date(2026, 6, 1)),
            _txn("SYNTH TRANSPORT", amount="-30.00", d=date(2026, 6, 2)),
            _txn("SYNTH SALARY", amount="3000.00", d=date(2026, 6, 3)),
            _txn("SYNTH REFUND", amount="20.00", d=date(2026, 6, 4)),
        ]
        r = _result(*txns)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Groceries",
                r.fingerprints[1]: "Transport",
                r.fingerprints[2]: "Income",
                r.fingerprints[3]: "Entertainment",  # net-positive category this month
            })
            result = store.category_trend(months=1, end_month="2026-06")

        assert result["spend_by_month"] == ["130.00"]
        for v in result["spend_by_month"]:
            assert isinstance(v, str)

    # -- clamp --------------------------------------------------------------

    def test_clamp_months_over_24(self) -> None:
        t = _txn("SYNTH ITEM", amount="-10.00", d=date(2026, 6, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t))
            result = store.category_trend(months=100)
        assert result["window"] == 24
        assert len(result["months"]) == 24

    def test_clamp_months_zero_or_negative_to_one(self) -> None:
        t = _txn("SYNTH ITEM", amount="-10.00", d=date(2026, 6, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t))
            result_zero = store.category_trend(months=0)
            result_neg = store.category_trend(months=-5)
        assert result_zero["window"] == 1
        assert len(result_zero["months"]) == 1
        assert result_neg["window"] == 1

    # -- end_month defaulting / earlier-than-all-data ------------------------

    def test_end_month_defaults_to_latest_year_month(self) -> None:
        t_may = _txn("SYNTH MAY", amount="-10.00", d=date(2026, 5, 1))
        t_jun = _txn("SYNTH JUN", amount="-20.00", d=date(2026, 6, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t_may, t_jun))
            result = store.category_trend()
        assert result["end_month"] == "2026-06"

    def test_end_month_earlier_than_all_data_returns_empty_series(self) -> None:
        t = _txn("SYNTH JUN", amount="-20.00", d=date(2026, 6, 1))
        with Store(":memory:") as store:
            store.add_new(_result(t))
            result = store.category_trend(months=3, end_month="2020-01")

        assert result["series"] == []
        assert result["spend_by_month"] == ["0.00", "0.00", "0.00"]
        assert result["months"] == ["2019-11", "2019-12", "2020-01"]
        # months_available is a GLOBAL count, independent of the requested window
        assert result["months_available"] == 1

    # -- year-boundary window -------------------------------------------------

    def test_year_boundary_window_dec_to_jan(self) -> None:
        t_nov = _txn("SYNTH NOV", amount="-10.00", d=date(2025, 11, 5))
        t_dec = _txn("SYNTH DEC", amount="-20.00", d=date(2025, 12, 5))
        t_jan = _txn("SYNTH JAN", amount="-30.00", d=date(2026, 1, 5))
        r = _result(t_nov, t_dec, t_jan)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({fp: "Groceries" for fp in r.fingerprints})
            result = store.category_trend(months=3, end_month="2026-01")

        assert result["months"] == ["2025-11", "2025-12", "2026-01"]
        groceries = result["series"][0]
        assert groceries["category"] == "Groceries"
        assert groceries["values"] == ["-10.00", "-20.00", "-30.00"]

    # -- NULL category accumulation -------------------------------------------

    def test_null_category_rows_accumulate_under_uncategorised(self) -> None:
        t1 = _txn("SYNTH NULL ONE", amount="-5.00", d=date(2026, 6, 1))
        t2 = _txn("SYNTH NULL TWO", amount="-7.50", d=date(2026, 6, 2))
        r = _result(t1, t2)
        with Store(":memory:") as store:
            store.add_new(r)
            # Never call set_categories — both remain category IS NULL.
            result = store.category_trend(months=1, end_month="2026-06")

        assert len(result["series"]) == 1
        assert result["series"][0]["category"] == "Uncategorised"
        assert result["series"][0]["values"] == ["-12.50"]

    # -- money contract: no float leaks anywhere ------------------------------

    def test_no_floats_anywhere_in_result(self) -> None:
        t = _txn("SYNTH ITEM", amount="-12.34", d=date(2026, 6, 1))
        r = _result(t)
        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({r.fingerprints[0]: "Groceries"})
            result = store.category_trend(months=1, end_month="2026-06")

        def _assert_no_float(obj) -> None:
            assert not isinstance(obj, float), f"float leaked into category_trend(): {obj!r}"
            if isinstance(obj, dict):
                for v in obj.values():
                    _assert_no_float(v)
            elif isinstance(obj, list):
                for v in obj:
                    _assert_no_float(v)

        _assert_no_float(result)


# ---------------------------------------------------------------------------
# TestPushSubscriptionStore — v2 Pass 3 (inert scaffold; LOCAL-ONLY storage)
#
# All endpoints/keys below are SYNTHETIC — never real Web Push subscription
# data. This module is exercised purely as local SQLite storage; no network
# code is involved anywhere in this class.
# ---------------------------------------------------------------------------


_SYNTH_SUB_A = {
    "endpoint": "https://example.test/push/AAA",
    "keys": {"p256dh": "k_p256dh_a", "auth": "k_auth_a"},
}
_SYNTH_SUB_B = {
    "endpoint": "https://example.test/push/BBB",
    "keys": {"p256dh": "k_p256dh_b", "auth": "k_auth_b"},
}


class TestPushSubscriptionStore:
    def test_upsert_then_list_returns_shape(self) -> None:
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB_A)
            subs = store.list_push_subscriptions()

        assert subs == [_SYNTH_SUB_A]

    def test_list_empty_on_fresh_store(self) -> None:
        with Store(":memory:") as store:
            assert store.list_push_subscriptions() == []

    def test_upsert_two_distinct_endpoints_both_listed(self) -> None:
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB_A)
            store.upsert_push_subscription(_SYNTH_SUB_B)
            subs = store.list_push_subscriptions()

        endpoints = {s["endpoint"] for s in subs}
        assert endpoints == {_SYNTH_SUB_A["endpoint"], _SYNTH_SUB_B["endpoint"]}
        assert len(subs) == 2

    def test_upsert_same_endpoint_twice_updates_keys_no_duplicate(self) -> None:
        updated = {
            "endpoint": _SYNTH_SUB_A["endpoint"],
            "keys": {"p256dh": "k_p256dh_a_UPDATED", "auth": "k_auth_a_UPDATED"},
        }
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB_A)
            store.upsert_push_subscription(updated)
            subs = store.list_push_subscriptions()

        assert len(subs) == 1, "re-subscribing the same endpoint must not duplicate the row"
        assert subs[0]["keys"] == updated["keys"]

    def test_delete_returns_one_then_zero_idempotent(self) -> None:
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB_A)
            first = store.delete_push_subscription(_SYNTH_SUB_A["endpoint"])
            second = store.delete_push_subscription(_SYNTH_SUB_A["endpoint"])

        assert first == 1
        assert second == 0

    def test_delete_leaves_list_empty(self) -> None:
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB_A)
            store.delete_push_subscription(_SYNTH_SUB_A["endpoint"])
            assert store.list_push_subscriptions() == []

    def test_delete_unknown_endpoint_is_safe_no_op(self) -> None:
        with Store(":memory:") as store:
            result = store.delete_push_subscription("https://example.test/push/NEVER_STORED")
        assert result == 0

    def test_upsert_missing_endpoint_raises_value_error(self) -> None:
        bad = {"keys": {"p256dh": "x", "auth": "y"}}
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.upsert_push_subscription(bad)

    def test_upsert_missing_p256dh_raises_value_error(self) -> None:
        bad = {"endpoint": "https://example.test/push/CCC", "keys": {"auth": "y"}}
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.upsert_push_subscription(bad)

    def test_upsert_missing_auth_raises_value_error(self) -> None:
        bad = {"endpoint": "https://example.test/push/DDD", "keys": {"p256dh": "x"}}
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.upsert_push_subscription(bad)

    def test_upsert_missing_keys_dict_entirely_raises_value_error(self) -> None:
        bad = {"endpoint": "https://example.test/push/EEE"}
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.upsert_push_subscription(bad)

    def test_failed_upsert_does_not_create_a_row(self) -> None:
        bad = {"endpoint": "https://example.test/push/FFF", "keys": {"p256dh": "x"}}
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.upsert_push_subscription(bad)
            assert store.list_push_subscriptions() == []


# ---------------------------------------------------------------------------
# TestTransactionsForCategory — dashboard drill-down view
# ---------------------------------------------------------------------------

class TestTransactionsForCategory:
    """store.transactions_for_category(): one category's rows for a month."""

    def _seed(self, store: Store):
        # Same month (_SYNTH_DATE -> 2026-06). Two Subscriptions, one left NULL.
        a = _txn("SYNTH STREAMING", "-15.99")
        b = _txn("SYNTH BIG SUB", "-170.01")
        c = _txn("SYNTH MYSTERY", "-42.00")
        store.add_new(_result(a, b, c))
        store.set_categories(
            {
                transaction_fingerprint(a): "Subscriptions",
                transaction_fingerprint(b): "Subscriptions",
                # c intentionally left uncategorised (category IS NULL)
            }
        )

    def test_matching_rows_sorted_by_magnitude_desc(self):
        with Store(":memory:") as store:
            self._seed(store)
            res = store.transactions_for_category("Subscriptions", "2026-06")
        assert res["category"] == "Subscriptions"
        assert res["month"] == "2026-06"
        assert res["count"] == 2
        assert [t["amount"] for t in res["transactions"]] == ["-170.01", "-15.99"]
        assert res["total"] == "-186.00"
        for t in res["transactions"]:
            assert set(t.keys()) == {"id", "date", "description", "amount", "bank"}
            assert isinstance(t["id"], int)

    def test_uncategorised_label_selects_null_category_rows(self):
        with Store(":memory:") as store:
            self._seed(store)
            res = store.transactions_for_category("Uncategorised", "2026-06")
        assert res["count"] == 1
        assert res["transactions"][0]["description"] == "SYNTH MYSTERY"

    def test_empty_category_has_defined_shape(self):
        with Store(":memory:") as store:
            self._seed(store)
            res = store.transactions_for_category("Transport", "2026-06")
        assert res == {
            "category": "Transport",
            "month": "2026-06",
            "total": "0.00",
            "count": 0,
            "transactions": [],
        }

    def test_empty_db_returns_null_month(self):
        with Store(":memory:") as store:
            res = store.transactions_for_category("Subscriptions")
        assert res["month"] is None
        assert res["count"] == 0
        assert res["transactions"] == []


# ---------------------------------------------------------------------------
# TestCorrections — manual category corrections + few-shot recall
# ---------------------------------------------------------------------------


class TestCorrections:
    """record_correction / recent_corrections — LOCAL-ONLY, dedupe/replace, newest-first."""

    def test_record_and_recent_roundtrip(self):
        with Store(":memory:") as store:
            store.record_correction("SYNTH CORNER STORE", "Dining Out")
            store.record_correction("SYNTH RIDESHARE CO", "Transport")
            recent = store.recent_corrections()
        # Newest first: the second insert leads.
        assert recent == [
            ("SYNTH RIDESHARE CO", "Transport"),
            ("SYNTH CORNER STORE", "Dining Out"),
        ]

    def test_empty_store_returns_empty_list(self):
        with Store(":memory:") as store:
            assert store.recent_corrections() == []

    def test_replace_on_repeat_keeps_latest_category(self):
        with Store(":memory:") as store:
            store.record_correction("SYNTH CAFE", "Groceries")
            store.record_correction("SYNTH CAFE", "Dining Out")
            recent = store.recent_corrections()
        # Single row for the repeated description, holding the LATEST category.
        assert recent == [("SYNTH CAFE", "Dining Out")]

    def test_junk_category_coerced_to_other(self):
        with Store(":memory:") as store:
            store.record_correction("SYNTH THING", "NotACategory")
            recent = store.recent_corrections()
        assert recent == [("SYNTH THING", "Other")]

    def test_limit_caps_and_orders_newest_first(self):
        with Store(":memory:") as store:
            for i in range(5):
                store.record_correction(f"SYNTH MERCHANT {i}", "Other")
            recent = store.recent_corrections(limit=3)
        assert len(recent) == 3
        assert [d for d, _ in recent] == [
            "SYNTH MERCHANT 4",
            "SYNTH MERCHANT 3",
            "SYNTH MERCHANT 2",
        ]

    def test_transaction_description_by_id_and_fingerprint(self):
        txn = _txn(desc="SYNTH LOOKUP MERCHANT", amount="-5.00")
        with Store(":memory:") as store:
            store.add_new(_result(txn))
            row = store.uncategorised()[0]
            assert store.transaction_description(row.id) == "SYNTH LOOKUP MERCHANT"
            assert (
                store.transaction_description(row.txn_fingerprint)
                == "SYNTH LOOKUP MERCHANT"
            )
            assert store.transaction_description(999999) is None
            assert store.transaction_description("no-such-fingerprint") is None
