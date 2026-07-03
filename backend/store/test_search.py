"""test_search.py — pytest suite for Store.search_transactions + the FTS5 index.

ALL fixtures use synthetic data generated inline. No real transactions, no real
descriptions, no real account numbers. Every database is :memory: or tmp_path —
NEVER the real SQLITE_PATH / ./data/. No network calls anywhere in this file.

Covers: FTS happy path, description- and category-token matches, the year_month
filter, blank/whitespace/no-result empty shapes, injection-safe special characters,
recency ordering, trigger sync on update + wipe, the migration/backfill path on
reopening a pre-existing DB, and the LIKE fallback (self._fts = False).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.data_source import Bank, Transaction
from backend.idempotency import NewTxnResult, transaction_fingerprint
from backend.store import Store


# ---------------------------------------------------------------------------
# Synthetic helpers — invented values; never real data
# ---------------------------------------------------------------------------

def _txn(
    desc: str,
    amount: str = "-10.00",
    d: date = date(2026, 6, 10),
    bank: Bank = Bank.COMMBANK,
    balance: str | None = None,
) -> Transaction:
    """Build one synthetic Transaction (no real data touched)."""
    return Transaction(
        date=d,
        description=desc,
        amount=Decimal(amount),
        bank=bank,
        balance=Decimal(balance) if balance is not None else None,
    )


def _result(*txns: Transaction) -> NewTxnResult:
    """Wrap synthetic transactions into a NewTxnResult with real fingerprints."""
    fps = tuple(transaction_fingerprint(t) for t in txns)
    return NewTxnResult(
        new_transactions=tuple(txns),
        fingerprints=fps,
        duplicates_in_batch=0,
    )


def _seed(store: Store, txns: list[Transaction], categories: dict[str, str] | None = None) -> None:
    """Insert txns and (optionally) categorise them by description.

    `categories` maps a transaction description to a category label; each matched
    row is updated via set_categories(id, label) so the FTS triggers fire.
    """
    store.add_new(_result(*txns))
    if not categories:
        return
    mapping: dict[int, str] = {}
    for row in store.conn.execute("SELECT id, description FROM transactions").fetchall():
        label = categories.get(row["description"])
        if label is not None:
            mapping[row["id"]] = label
    if mapping:
        store.set_categories(mapping)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------------
# TestFtsAvailable — the primary path is FTS5 on the bundled sqlite build
# ---------------------------------------------------------------------------

class TestFtsAvailable:
    def test_fts_flag_true_on_memory_store(self, store):
        """CPython's bundled sqlite ships FTS5, so the primary path is exercised."""
        assert store._fts is True


# ---------------------------------------------------------------------------
# TestHappyPath
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_returns_matching_rows(self, store):
        _seed(
            store,
            [
                _txn("SYNTH COFFEE HOUSE", "-4.50"),
                _txn("SYNTH BOOKSTORE", "-30.00"),
                _txn("UNRELATED MERCHANT", "-12.00"),
            ],
        )
        result = store.search_transactions("SYNTH")
        descs = {t["description"] for t in result["transactions"]}
        assert descs == {"SYNTH COFFEE HOUSE", "SYNTH BOOKSTORE"}

    def test_row_key_shape(self, store):
        _seed(store, [_txn("SYNTH COFFEE HOUSE", "-4.50")], {"SYNTH COFFEE HOUSE": "Dining Out"})
        row = store.search_transactions("SYNTH")["transactions"][0]
        assert set(row.keys()) == {"id", "date", "description", "amount", "bank", "category"}

    def test_top_level_shape(self, store):
        _seed(store, [_txn("SYNTH COFFEE HOUSE", "-4.50")])
        result = store.search_transactions("SYNTH")
        assert set(result.keys()) == {"query", "month", "total", "count", "transactions"}
        assert result["query"] == "SYNTH"
        assert result["month"] is None

    def test_amounts_are_canonical_str_decimal(self, store):
        _seed(store, [_txn("SYNTH COFFEE HOUSE", "-4.50")])
        row = store.search_transactions("SYNTH")["transactions"][0]
        assert row["amount"] == "-4.50"
        assert isinstance(row["amount"], str)

    def test_count_equals_len_transactions(self, store):
        _seed(store, [_txn("SYNTH A"), _txn("SYNTH B"), _txn("SYNTH C")])
        result = store.search_transactions("SYNTH")
        assert result["count"] == len(result["transactions"]) == 3

    def test_total_is_decimal_sum_of_returned_rows(self, store):
        _seed(
            store,
            [
                _txn("SYNTH A", "-4.50"),
                _txn("SYNTH B", "-30.00"),
                _txn("SYNTH C", "-12.25"),
            ],
        )
        result = store.search_transactions("SYNTH")
        assert result["total"] == str(Decimal("-4.50") + Decimal("-30.00") + Decimal("-12.25"))

    def test_prefix_match(self, store):
        """A partial token prefix-matches (implicit trailing '*')."""
        _seed(store, [_txn("SYNTH SUPERMARKET", "-55.00")])
        result = store.search_transactions("SUPERMAR")
        assert result["count"] == 1

    def test_implicit_and_across_tokens(self, store):
        _seed(
            store,
            [
                _txn("SYNTH ORANGE JUICE", "-3.00"),
                _txn("SYNTH APPLE PIE", "-6.00"),
            ],
        )
        # Both tokens must be present in a row (AND), so only ORANGE JUICE matches.
        result = store.search_transactions("SYNTH ORANGE")
        assert {t["description"] for t in result["transactions"]} == {"SYNTH ORANGE JUICE"}


# ---------------------------------------------------------------------------
# TestCategoryTokenMatch — the index covers category as well as description
# ---------------------------------------------------------------------------

class TestCategoryTokenMatch:
    def test_category_label_is_searchable(self, store):
        _seed(
            store,
            [
                _txn("ALPHA MERCHANT", "-10.00"),
                _txn("BETA MERCHANT", "-20.00"),
            ],
            {"ALPHA MERCHANT": "Subscriptions", "BETA MERCHANT": "Groceries"},
        )
        result = store.search_transactions("Subscriptions")
        assert {t["description"] for t in result["transactions"]} == {"ALPHA MERCHANT"}


# ---------------------------------------------------------------------------
# TestMonthFilter
# ---------------------------------------------------------------------------

class TestMonthFilter:
    def test_year_month_narrows_results(self, store):
        _seed(
            store,
            [
                _txn("SYNTH JUNE BUY", "-10.00", d=date(2026, 6, 5)),
                _txn("SYNTH MAY BUY", "-20.00", d=date(2026, 5, 5)),
            ],
        )
        result = store.search_transactions("SYNTH", year_month="2026-06")
        assert result["month"] == "2026-06"
        assert {t["description"] for t in result["transactions"]} == {"SYNTH JUNE BUY"}

    def test_year_month_no_match_returns_empty(self, store):
        _seed(store, [_txn("SYNTH JUNE BUY", "-10.00", d=date(2026, 6, 5))])
        result = store.search_transactions("SYNTH", year_month="2026-01")
        assert result["count"] == 0
        assert result["total"] == "0.00"


# ---------------------------------------------------------------------------
# TestEmptyAndNoResults
# ---------------------------------------------------------------------------

class TestEmptyAndNoResults:
    @pytest.mark.parametrize("q", ["", "   ", "\t", "\n  \n"])
    def test_blank_query_is_empty_shape(self, store, q):
        _seed(store, [_txn("SYNTH COFFEE HOUSE", "-4.50")])
        result = store.search_transactions(q)
        assert result["count"] == 0
        assert result["transactions"] == []
        assert result["total"] == "0.00"
        assert result["query"] == q

    def test_blank_query_does_not_hit_db(self, store):
        """A blank query short-circuits before touching the connection at all."""
        class _BoomConn:
            def __getattr__(self, name):
                raise AssertionError(
                    "search_transactions must not touch the DB on a blank query"
                )

        real_conn = store.conn
        store.conn = _BoomConn()  # any DB access now raises
        try:
            result = store.search_transactions("   ")
        finally:
            store.conn = real_conn  # restore so fixture teardown can close cleanly
        assert result["count"] == 0
        assert result["transactions"] == []

    def test_no_results_query(self, store):
        _seed(store, [_txn("SYNTH COFFEE HOUSE", "-4.50")])
        result = store.search_transactions("NONEXISTENTTOKENXYZ")
        assert result["count"] == 0
        assert result["transactions"] == []
        assert result["total"] == "0.00"


# ---------------------------------------------------------------------------
# TestSpecialCharacters — quoting must neutralise FTS operators/syntax
# ---------------------------------------------------------------------------

class TestSpecialCharacters:
    @pytest.mark.parametrize(
        "q",
        [
            'foo"bar',
            "a AND b",
            "a OR b",
            "NEAR(",
            "%_",
            "*",
            '"',
            "col:val",
            "^caret",
            "(unbalanced",
            "a* b*",
        ],
    )
    def test_special_query_never_raises(self, store, q):
        _seed(
            store,
            [
                _txn("SYNTH COFFEE HOUSE", "-4.50"),
                _txn("SYNTH BOOKSTORE", "-30.00"),
            ],
        )
        result = store.search_transactions(q)
        # A valid (possibly empty) shape, never an exception / injected operator.
        assert set(result.keys()) == {"query", "month", "total", "count", "transactions"}
        assert isinstance(result["count"], int)
        assert result["count"] == len(result["transactions"])


# ---------------------------------------------------------------------------
# TestOrdering — recency: date DESC, id DESC
# ---------------------------------------------------------------------------

class TestOrdering:
    def test_orders_by_date_desc_then_id_desc(self, store):
        _seed(
            store,
            [
                _txn("SYNTH OLDEST", "-1.00", d=date(2026, 6, 1)),
                _txn("SYNTH SAMEDAY A", "-2.00", d=date(2026, 6, 15)),
                _txn("SYNTH SAMEDAY B", "-3.00", d=date(2026, 6, 15)),
                _txn("SYNTH NEWEST", "-4.00", d=date(2026, 6, 20)),
            ],
        )
        descs = [t["description"] for t in store.search_transactions("SYNTH")["transactions"]]
        # 2026-06-20 first; within 2026-06-15 the later-inserted (higher id) B before A.
        assert descs == ["SYNTH NEWEST", "SYNTH SAMEDAY B", "SYNTH SAMEDAY A", "SYNTH OLDEST"]


# ---------------------------------------------------------------------------
# TestTriggerSync — the FTS index stays current across writes
# ---------------------------------------------------------------------------

class TestTriggerSync:
    def test_recategorise_updates_index(self, store):
        _seed(
            store,
            [_txn("GAMMA MERCHANT", "-10.00")],
            {"GAMMA MERCHANT": "Groceries"},
        )
        # Old label finds it; new label does not (yet).
        assert store.search_transactions("Groceries")["count"] == 1
        assert store.search_transactions("Transport")["count"] == 0

        row_id = store.conn.execute(
            "SELECT id FROM transactions WHERE description = ?", ("GAMMA MERCHANT",)
        ).fetchone()["id"]
        store.set_categories({row_id: "Transport"})

        # AFTER UPDATE trigger moved the row in the index.
        assert store.search_transactions("Transport")["count"] == 1
        assert store.search_transactions("Groceries")["count"] == 0

    def test_reset_all_data_empties_index(self, store):
        _seed(store, [_txn("SYNTH COFFEE HOUSE", "-4.50"), _txn("SYNTH BOOKSTORE", "-30.00")])
        assert store.search_transactions("SYNTH")["count"] == 2
        store.reset_all_data()
        result = store.search_transactions("SYNTH")
        assert result["count"] == 0
        assert result["transactions"] == []


# ---------------------------------------------------------------------------
# TestMigrationBackfill — a pre-existing DB gains the index + backfills on reopen
# ---------------------------------------------------------------------------

class TestMigrationBackfill:
    def test_reopen_recreates_and_backfills_index(self, tmp_path):
        db = str(tmp_path / "search.sqlite")

        store = Store(db)
        _seed(store, [_txn("SYNTH LEGACY ROW", "-42.00")])
        assert store.search_transactions("LEGACY")["count"] == 1

        # Simulate a DB that predates the feature: drop the index + its triggers.
        store.conn.execute("DROP TABLE transactions_fts")
        store.conn.execute("DROP TRIGGER transactions_fts_ai")
        store.conn.execute("DROP TRIGGER transactions_fts_ad")
        store.conn.execute("DROP TRIGGER transactions_fts_au")
        store.conn.commit()
        store.close()

        # Reopen: init_schema -> init_search_index recreates + 'rebuild' backfills.
        store2 = Store(db)
        result = store2.search_transactions("LEGACY")
        assert result["count"] == 1
        assert result["transactions"][0]["description"] == "SYNTH LEGACY ROW"
        store2.close()


# ---------------------------------------------------------------------------
# TestLikeFallback — self._fts = False path returns the identical shape
# ---------------------------------------------------------------------------

class TestLikeFallback:
    def test_fallback_returns_same_rows_for_happy_query(self, store):
        _seed(
            store,
            [
                _txn("SYNTH COFFEE HOUSE", "-4.50"),
                _txn("SYNTH BOOKSTORE", "-30.00"),
                _txn("UNRELATED MERCHANT", "-12.00"),
            ],
        )
        store._fts = False  # force the LIKE fallback
        result = store.search_transactions("SYNTH")
        assert {t["description"] for t in result["transactions"]} == {
            "SYNTH COFFEE HOUSE",
            "SYNTH BOOKSTORE",
        }
        assert result["count"] == 2
        assert result["total"] == str(Decimal("-4.50") + Decimal("-30.00"))

    def test_fallback_blank_query_is_empty_shape(self, store):
        _seed(store, [_txn("SYNTH COFFEE HOUSE", "-4.50")])
        store._fts = False
        result = store.search_transactions("   ")
        assert result["count"] == 0
        assert result["total"] == "0.00"

    def test_fallback_matches_category(self, store):
        _seed(
            store,
            [_txn("DELTA MERCHANT", "-10.00")],
            {"DELTA MERCHANT": "Subscriptions"},
        )
        store._fts = False
        assert store.search_transactions("Subscriptions")["count"] == 1

    def test_fallback_escapes_like_wildcards(self, store):
        """A '%' in the query is a literal, not a match-everything wildcard."""
        _seed(
            store,
            [
                _txn("SYNTH 50% OFF SALE", "-5.00"),
                _txn("SYNTH PLAIN MERCHANT", "-9.00"),
            ],
        )
        store._fts = False
        result = store.search_transactions("50%")
        # Only the row that literally contains '50%' matches (not the plain row).
        assert {t["description"] for t in result["transactions"]} == {"SYNTH 50% OFF SALE"}

    def test_fallback_ordering_matches_fts(self, store):
        _seed(
            store,
            [
                _txn("SYNTH OLDEST", "-1.00", d=date(2026, 6, 1)),
                _txn("SYNTH NEWEST", "-4.00", d=date(2026, 6, 20)),
            ],
        )
        store._fts = False
        descs = [t["description"] for t in store.search_transactions("SYNTH")["transactions"]]
        assert descs == ["SYNTH NEWEST", "SYNTH OLDEST"]
