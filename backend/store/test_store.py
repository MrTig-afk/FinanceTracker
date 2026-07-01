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

# ---------------------------------------------------------------------------
# Synthetic helpers — invented values; never real data
# ---------------------------------------------------------------------------

_SYNTH_DATE = date(2026, 6, 10)  # fixed synthetic date; not meaningful


def _txn(
    desc: str = "SYNTH MERCHANT A",
    amount: str = "-10.00",
    d: date = _SYNTH_DATE,
    bank: Bank = Bank.COMMBANK,
) -> Transaction:
    """Build a synthetic Transaction without touching any real data."""
    return Transaction(date=d, description=desc, amount=Decimal(amount), bank=bank)


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
        }

    def test_summary_mixed_income_and_expense(self) -> None:
        """net is the signed arithmetic sum; income offsets expenses exactly."""
        t_expense = _txn("SYNTH ELECTRICITY BILL", amount="-200.00", d=date(2026, 6, 1))
        t_income = _txn("SYNTH SALARY CREDIT", amount="2000.00", d=date(2026, 6, 2))
        r = _result(t_expense, t_income)

        with Store(":memory:") as store:
            store.add_new(r)
            store.set_categories({
                r.fingerprints[0]: "Utilities",
                r.fingerprints[1]: "Income",
            })
            result = store.summary("2026-06")

        assert result["totals"]["Utilities"] == "-200.00"
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
