"""test_transfer_rule.py — pytest suite for internal-transfer netting (v6 feature 2).

Covers two layers:
  1. The pure ``pair_transfers`` matcher in ``backend/store/transfer_rule.py``
     (deterministic greedy one-to-one; cross-bank; exact opposite Decimal magnitude;
     0..3 day absolute window; order-independent).
  2. ``Store.detect_transfers`` / ``list_transfer_pairs`` / ``untag_transfer_pair``
     (tagging both legs 'Transfer', prev-category capture + restore, idempotency,
     dismissed-never-rematched, FTS-index sync).

ALL fixtures are SYNTHETIC, generated inline — never real transactions or CSVs.
Every database is ``:memory:``; there is ZERO network code anywhere in this feature.
"""
from __future__ import annotations

import itertools
import random
from datetime import date
from decimal import Decimal

import pytest

from backend.data_source import Bank, Transaction
from backend.idempotency import NewTxnResult, transaction_fingerprint
from backend.store import Store, TransferDetectResult
from backend.store.schema import search_index_available
from backend.store.transfer_rule import (
    MAX_WINDOW_DAYS,
    TRANSFER_CATEGORY,
    CandidateRow,
    pair_transfers,
)

_COMMBANK = Bank.COMMBANK.value  # 'commbank'
_WESTPAC = Bank.WESTPAC.value    # 'westpac'


# ---------------------------------------------------------------------------
# Pure pair_transfers — synthetic CandidateRows built directly.
# ---------------------------------------------------------------------------


def _row(id_: int, d: str, amount: str, bank: str) -> CandidateRow:
    return CandidateRow(id=id_, date=d, amount=Decimal(amount), bank=bank)


class TestPairTransfersHappyPath:
    def test_cross_bank_opposite_pair_matches_debit_first(self):
        rows = [
            _row(1, "2026-06-01", "-500.00", _COMMBANK),
            _row(2, "2026-06-02", "500.00", _WESTPAC),
        ]
        assert pair_transfers(rows) == [(1, 2)]

    def test_credit_may_post_first(self):
        # Credit dated BEFORE the debit, still within window -> matched (absolute diff).
        rows = [
            _row(1, "2026-06-05", "-500.00", _COMMBANK),
            _row(2, "2026-06-03", "500.00", _WESTPAC),
        ]
        assert pair_transfers(rows) == [(1, 2)]

    def test_zero_day_gap_matches(self):
        rows = [
            _row(1, "2026-06-01", "-120.00", _WESTPAC),
            _row(2, "2026-06-01", "120.00", _COMMBANK),
        ]
        assert pair_transfers(rows) == [(1, 2)]


class TestPairTransfersNonMatches:
    def test_same_bank_not_matched(self):
        rows = [
            _row(1, "2026-06-01", "-500.00", _COMMBANK),
            _row(2, "2026-06-02", "500.00", _COMMBANK),
        ]
        assert pair_transfers(rows) == []

    def test_unequal_magnitude_not_matched(self):
        rows = [
            _row(1, "2026-06-01", "-500.00", _COMMBANK),
            _row(2, "2026-06-02", "500.01", _WESTPAC),
        ]
        assert pair_transfers(rows) == []

    def test_same_sign_never_matched(self):
        # Two debits, or two credits, can never form a signed transfer pair.
        two_debits = [
            _row(1, "2026-06-01", "-500.00", _COMMBANK),
            _row(2, "2026-06-02", "-500.00", _WESTPAC),
        ]
        two_credits = [
            _row(1, "2026-06-01", "500.00", _COMMBANK),
            _row(2, "2026-06-02", "500.00", _WESTPAC),
        ]
        assert pair_transfers(two_debits) == []
        assert pair_transfers(two_credits) == []

    def test_zero_amounts_never_matched(self):
        rows = [
            _row(1, "2026-06-01", "0.00", _COMMBANK),
            _row(2, "2026-06-02", "0.00", _WESTPAC),
        ]
        assert pair_transfers(rows) == []


class TestPairTransfersWindowBoundary:
    def test_exactly_three_days_matches(self):
        rows = [
            _row(1, "2026-06-01", "-500.00", _COMMBANK),
            _row(2, "2026-06-04", "500.00", _WESTPAC),  # 3 days
        ]
        assert pair_transfers(rows) == [(1, 2)]

    def test_four_days_not_matched(self):
        rows = [
            _row(1, "2026-06-01", "-500.00", _COMMBANK),
            _row(2, "2026-06-05", "500.00", _WESTPAC),  # 4 days
        ]
        assert pair_transfers(rows) == []

    def test_window_constant_is_three(self):
        assert MAX_WINDOW_DAYS == 3


class TestPairTransfersTieBreak:
    def test_closest_date_wins(self):
        # One debit; two candidate credits at day+2 and day+1 -> the day+1 wins.
        rows = [
            _row(1, "2026-06-01", "-500.00", _COMMBANK),
            _row(2, "2026-06-03", "500.00", _WESTPAC),  # distance 2
            _row(3, "2026-06-02", "500.00", _WESTPAC),  # distance 1 (closer)
        ]
        assert pair_transfers(rows) == [(1, 3)]

    def test_equal_distance_lowest_id_wins(self):
        # Two credits equidistant from the debit -> the lower id is chosen.
        rows = [
            _row(1, "2026-06-02", "-500.00", _COMMBANK),
            _row(2, "2026-06-01", "500.00", _WESTPAC),  # distance 1
            _row(3, "2026-06-03", "500.00", _WESTPAC),  # distance 1
        ]
        assert pair_transfers(rows) == [(1, 2)]

    def test_shuffled_input_is_deterministic(self):
        base = [
            _row(1, "2026-06-01", "-500.00", _COMMBANK),
            _row(2, "2026-06-02", "500.00", _WESTPAC),
            _row(3, "2026-06-10", "-75.00", _COMMBANK),
            _row(4, "2026-06-11", "75.00", _WESTPAC),
        ]
        expected = pair_transfers(base)
        assert expected == [(1, 2), (3, 4)]
        rng = random.Random(1234)
        for _ in range(20):
            shuffled = base[:]
            rng.shuffle(shuffled)
            assert pair_transfers(shuffled) == expected


class TestPairTransfersOneToOne:
    def test_two_debits_one_credit_one_pair_no_double(self):
        rows = [
            _row(1, "2026-06-01", "-500.00", _COMMBANK),
            _row(2, "2026-06-02", "-500.00", _COMMBANK),
            _row(3, "2026-06-01", "500.00", _WESTPAC),  # equidistant-ish; closest wins
        ]
        pairs = pair_transfers(rows)
        assert len(pairs) == 1
        used = {i for pair in pairs for i in pair}
        # No row appears in more than one pair.
        assert len(used) == 2 * len(pairs)

    def test_never_raises_on_empty(self):
        assert pair_transfers([]) == []


# ---------------------------------------------------------------------------
# Store.detect_transfers / list_transfer_pairs / untag_transfer_pair
# ---------------------------------------------------------------------------

_fp_counter = itertools.count()


def _insert(store: Store, *, d: str, amount: str, bank: str,
            desc: str = "SYNTH TXN", category: str | None = None) -> int:
    """Insert one synthetic transaction row directly; return its primary-key id."""
    fp = f"fp-{next(_fp_counter)}"
    cur = store.conn.execute(
        "INSERT INTO transactions"
        "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
        " VALUES (?,?,?,?,?,?,?,'t')",
        (fp, d, desc, amount, bank, category, d[:7]),
    )
    store.conn.commit()
    return cur.lastrowid


def _category(store: Store, row_id: int) -> str | None:
    return store.conn.execute(
        "SELECT category FROM transactions WHERE id = ?", (row_id,)
    ).fetchone()[0]


def _txn(desc: str, amount: str, d: date, bank: Bank) -> Transaction:
    return Transaction(date=d, description=desc, amount=Decimal(amount), bank=bank, balance=None)


def _result(*txns: Transaction) -> NewTxnResult:
    return NewTxnResult(
        new_transactions=tuple(txns),
        fingerprints=tuple(transaction_fingerprint(t) for t in txns),
        duplicates_in_batch=0,
    )


class TestDetectTransfers:
    def test_tags_both_legs_and_captures_prev_categories(self):
        with Store(":memory:") as store:
            out_id = _insert(store, d="2026-06-01", amount="-500.00", bank=_COMMBANK,
                             desc="SYNTH OUT", category="Other")
            in_id = _insert(store, d="2026-06-02", amount="500.00", bank=_WESTPAC,
                            desc="SYNTH IN", category=None)

            result = store.detect_transfers()

            assert result == TransferDetectResult(1, ("2026-06",))
            assert _category(store, out_id) == TRANSFER_CATEGORY
            assert _category(store, in_id) == TRANSFER_CATEGORY

            pair = store.conn.execute(
                "SELECT prev_category_out, prev_category_in, status FROM transfer_pairs"
            ).fetchone()
            assert pair["prev_category_out"] == "Other"
            assert pair["prev_category_in"] is None
            assert pair["status"] == "active"

    def test_affected_months_span_a_boundary(self):
        with Store(":memory:") as store:
            _insert(store, d="2026-05-31", amount="-500.00", bank=_COMMBANK)
            _insert(store, d="2026-06-02", amount="500.00", bank=_WESTPAC)  # 2 days later
            result = store.detect_transfers()
            assert result.pairs_created == 1
            assert result.affected_months == ("2026-05", "2026-06")

    def test_rerun_is_idempotent_no_op(self):
        with Store(":memory:") as store:
            out_id = _insert(store, d="2026-06-01", amount="-500.00", bank=_COMMBANK)
            in_id = _insert(store, d="2026-06-02", amount="500.00", bank=_WESTPAC)
            store.detect_transfers()

            second = store.detect_transfers()
            assert second == TransferDetectResult(0, ())
            # Categories unchanged; still exactly one pair row.
            assert _category(store, out_id) == TRANSFER_CATEGORY
            assert _category(store, in_id) == TRANSFER_CATEGORY
            (n,) = store.conn.execute("SELECT COUNT(*) FROM transfer_pairs").fetchone()
            assert n == 1

    def test_cross_ingest_pair_matches_on_second_detect(self):
        with Store(":memory:") as store:
            store.add_new(_result(
                _txn("SYNTH OUT", "-500.00", date(2026, 6, 1), Bank.COMMBANK)
            ))
            first = store.detect_transfers()
            assert first.pairs_created == 0  # only one leg present

            store.add_new(_result(
                _txn("SYNTH IN", "500.00", date(2026, 6, 2), Bank.WESTPAC)
            ))
            second = store.detect_transfers()
            assert second.pairs_created == 1

    def test_empty_db_no_writes_no_raise(self):
        with Store(":memory:") as store:
            result = store.detect_transfers()
            assert result == TransferDetectResult(0, ())
            (n,) = store.conn.execute("SELECT COUNT(*) FROM transfer_pairs").fetchone()
            assert n == 0

    def test_no_pair_when_only_non_matching_rows(self):
        with Store(":memory:") as store:
            _insert(store, d="2026-06-01", amount="-500.00", bank=_COMMBANK)
            _insert(store, d="2026-06-02", amount="500.00", bank=_COMMBANK)  # same bank
            assert store.detect_transfers() == TransferDetectResult(0, ())


class TestListTransferPairs:
    def test_shape_and_amount_magnitude(self):
        with Store(":memory:") as store:
            _insert(store, d="2026-06-01", amount="-500.00", bank=_COMMBANK, desc="SYNTH OUT")
            _insert(store, d="2026-06-02", amount="500.00", bank=_WESTPAC, desc="SYNTH IN")
            store.detect_transfers()

            pairs = store.list_transfer_pairs()
            assert len(pairs) == 1
            p = pairs[0]
            assert set(p.keys()) == {"id", "amount", "created_at", "out", "in"}
            assert p["amount"] == "500.00"  # positive magnitude of the credit leg
            assert p["out"]["amount"] == "-500.00"
            assert p["out"]["bank"] == _COMMBANK
            assert p["in"]["amount"] == "500.00"
            assert p["in"]["bank"] == _WESTPAC
            assert p["out"]["description"] == "SYNTH OUT"
            assert p["in"]["description"] == "SYNTH IN"

    def test_dismissed_pairs_excluded_from_list(self):
        with Store(":memory:") as store:
            _insert(store, d="2026-06-01", amount="-500.00", bank=_COMMBANK)
            _insert(store, d="2026-06-02", amount="500.00", bank=_WESTPAC)
            store.detect_transfers()
            pair_id = store.list_transfer_pairs()[0]["id"]
            store.untag_transfer_pair(pair_id)
            assert store.list_transfer_pairs() == []


class TestUntagTransferPair:
    def test_restore_previous_categories_including_null(self):
        with Store(":memory:") as store:
            out_id = _insert(store, d="2026-06-01", amount="-500.00", bank=_COMMBANK,
                             category="Groceries")
            in_id = _insert(store, d="2026-06-02", amount="500.00", bank=_WESTPAC,
                            category=None)
            store.detect_transfers()
            pair_id = store.list_transfer_pairs()[0]["id"]

            restored = store.untag_transfer_pair(pair_id)
            # Rich result: count + where each leg went (None = back to Uncategorised).
            assert restored == {"restored": 2, "out": "Groceries", "in": None}
            assert _category(store, out_id) == "Groceries"
            assert _category(store, in_id) is None  # NULL restored to NULL
            status = store.conn.execute(
                "SELECT status FROM transfer_pairs WHERE id = ?", (pair_id,)
            ).fetchone()[0]
            assert status == "dismissed"

    def test_second_untag_is_zero(self):
        with Store(":memory:") as store:
            _insert(store, d="2026-06-01", amount="-500.00", bank=_COMMBANK)
            _insert(store, d="2026-06-02", amount="500.00", bank=_WESTPAC)
            store.detect_transfers()
            pair_id = store.list_transfer_pairs()[0]["id"]
            assert store.untag_transfer_pair(pair_id)["restored"] == 2
            # Idempotent second call: nothing restored, no categories reported.
            assert store.untag_transfer_pair(pair_id) == {"restored": 0}

    def test_unknown_id_returns_none(self):
        with Store(":memory:") as store:
            assert store.untag_transfer_pair(999999) is None

    def test_dismissed_legs_never_rematched(self):
        with Store(":memory:") as store:
            _insert(store, d="2026-06-01", amount="-500.00", bank=_COMMBANK)
            _insert(store, d="2026-06-02", amount="500.00", bank=_WESTPAC)
            store.detect_transfers()
            pair_id = store.list_transfer_pairs()[0]["id"]
            store.untag_transfer_pair(pair_id)

            # A later detection must NOT re-tag the dismissed rows.
            again = store.detect_transfers()
            assert again == TransferDetectResult(0, ())
            (active,) = store.conn.execute(
                "SELECT COUNT(*) FROM transfer_pairs WHERE status = 'active'"
            ).fetchone()
            assert active == 0


class TestFtsSyncAfterTagging:
    def test_tagging_keeps_fts_index_in_sync(self):
        with Store(":memory:") as store:
            if not search_index_available(store.conn):
                pytest.skip("FTS5 unavailable on this SQLite build")

            _insert(store, d="2026-06-01", amount="-500.00", bank=_COMMBANK,
                    desc="SYNTHOUTTOKEN", category="Groceries")
            _insert(store, d="2026-06-02", amount="500.00", bank=_WESTPAC,
                    desc="SYNTHINTOKEN", category=None)

            # Before tagging: the pre-categorised leg is findable by its old category.
            assert store.search_transactions("Groceries")["count"] == 1

            store.detect_transfers()

            # After the direct UPDATE, the FTS trigger reindexed: 'Transfer' now finds
            # both legs, and the stale 'Groceries' category token no longer matches.
            assert store.search_transactions("Transfer")["count"] == 2
            assert store.search_transactions("Groceries")["count"] == 0
            # Descriptions are still indexed and searchable.
            assert store.search_transactions("SYNTHOUTTOKEN")["count"] == 1
