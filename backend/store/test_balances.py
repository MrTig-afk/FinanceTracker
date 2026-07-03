"""test_balances.py — pytest suite for the v7 net-position balance history.

Covers Store.record_month_balances(), Store.balance_series(), the reset wipe,
and the upsert idempotency / fail-safe semantics of the `balances` table.

ALL fixtures use synthetic data generated inline.
No real transactions, no real descriptions, no real account numbers.
No network calls anywhere in this file.
Every database is :memory: — NEVER the real SQLITE_PATH / ./data/.

Money assertions are made on the stored/served str(Decimal) TEXT values and are
verified to round-trip through Decimal — never through float.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.data_source import Bank, Transaction
from backend.idempotency import NewTxnResult, transaction_fingerprint
from backend.store import Store, amount_from_text


# ---------------------------------------------------------------------------
# Synthetic helpers — invented values; never real data
# ---------------------------------------------------------------------------


def _txn(
    desc: str = "SYNTH MERCHANT A",
    amount: str = "-10.00",
    d: date = date(2026, 6, 10),
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


def _stored(store: Store, bank: str, ym: str) -> str | None:
    """Return the stored closing_balance TEXT for a (bank, month), or None if absent."""
    row = store.conn.execute(
        "SELECT closing_balance FROM balances WHERE bank = ? AND year_month = ?",
        (bank, ym),
    ).fetchone()
    return row["closing_balance"] if row is not None else None


def _derived_at(store: Store, bank: str, ym: str) -> str | None:
    row = store.conn.execute(
        "SELECT derived_at FROM balances WHERE bank = ? AND year_month = ?",
        (bank, ym),
    ).fetchone()
    return row["derived_at"] if row is not None else None


def _count(store: Store) -> int:
    return store.conn.execute("SELECT COUNT(*) FROM balances").fetchone()[0]


# ---------------------------------------------------------------------------
# 1. Happy path — record_month_balances stores the derived closing per bank
# ---------------------------------------------------------------------------


class TestRecordMonthBalancesHappyPath:
    def test_both_banks_one_month(self) -> None:
        """A consistent chain for CommBank + a Westpac single row -> 2 rows, exact TEXT."""
        # CommBank chain: opening 100 -> 90 -> 70 -> 75 (closing 75.00).
        cb1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        cb2 = _txn("SYNTH SHOP B", amount="-20.00", d=date(2026, 6, 2), balance="70.00")
        cb3 = _txn("SYNTH SHOP C", amount="5.00", d=date(2026, 6, 3), balance="75.00")
        # Westpac single row, closing 200.00.
        wp1 = _txn(
            "SYNTH BILL", amount="-30.00", d=date(2026, 6, 4),
            bank=Bank.WESTPAC, balance="200.00",
        )
        with Store(":memory:") as store:
            store.add_new(_result(cb1, cb2, cb3, wp1))
            written = store.record_month_balances(["2026-06"])

            assert written == 2
            assert _stored(store, "commbank", "2026-06") == "75.00"
            assert _stored(store, "westpac", "2026-06") == "200.00"

    def test_stored_closing_roundtrips_through_decimal_not_float(self) -> None:
        cb1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        with Store(":memory:") as store:
            store.add_new(_result(cb1))
            store.record_month_balances(["2026-06"])
            stored = _stored(store, "commbank", "2026-06")

            assert isinstance(stored, str)
            assert not isinstance(stored, float)
            assert amount_from_text(stored) == Decimal("90.00")


# ---------------------------------------------------------------------------
# 2. Unavailable derivation writes nothing
# ---------------------------------------------------------------------------


class TestUnavailableDerivationWritesNothing:
    def test_null_balance_single_row_writes_nothing(self) -> None:
        """A row with NULL balance -> closing unavailable -> 0 written, table empty."""
        cb = _txn("SYNTH NO BALANCE", amount="-10.00", d=date(2026, 6, 1))  # balance None
        with Store(":memory:") as store:
            store.add_new(_result(cb))
            written = store.record_month_balances(["2026-06"])

            assert written == 0
            assert _count(store) == 0

    def test_inconsistent_chain_writes_nothing(self) -> None:
        """Two rows whose running-balance relation holds in NEITHER direction -> 0."""
        cb1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        cb2 = _txn("SYNTH SHOP B", amount="-20.00", d=date(2026, 6, 2), balance="5000.00")
        with Store(":memory:") as store:
            store.add_new(_result(cb1, cb2))
            written = store.record_month_balances(["2026-06"])

            assert written == 0
            assert _count(store) == 0


# ---------------------------------------------------------------------------
# 3. Failure never erases a previously stored value
# ---------------------------------------------------------------------------


class TestFailureNeverErases:
    def test_later_unavailable_derivation_keeps_last_good_value(self) -> None:
        cb1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        with Store(":memory:") as store:
            store.add_new(_result(cb1))
            first = store.record_month_balances(["2026-06"])
            assert first == 1
            assert _stored(store, "commbank", "2026-06") == "90.00"

            # Insert a chain-breaking row so the month's derivation now returns None.
            cb2 = _txn("SYNTH SHOP B", amount="-20.00", d=date(2026, 6, 2), balance="5000.00")
            store.add_new(_result(cb2))
            second = store.record_month_balances(["2026-06"])

            assert second == 0, "an unavailable re-derivation must write nothing"
            assert _stored(store, "commbank", "2026-06") == "90.00", "stored value must survive"


# ---------------------------------------------------------------------------
# 4. Upsert idempotency — unchanged re-record writes nothing (WHERE guard)
# ---------------------------------------------------------------------------


class TestUpsertIdempotency:
    def test_second_unchanged_record_writes_zero_and_preserves_derived_at(self) -> None:
        cb1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        with Store(":memory:") as store:
            store.add_new(_result(cb1))
            first = store.record_month_balances(["2026-06"])
            stamp_after_first = _derived_at(store, "commbank", "2026-06")

            second = store.record_month_balances(["2026-06"])
            stamp_after_second = _derived_at(store, "commbank", "2026-06")

            assert first == 1
            assert second == 0, "unchanged re-record must write 0 rows (WHERE guard)"
            assert stamp_after_second == stamp_after_first, "derived_at must be untouched"


# ---------------------------------------------------------------------------
# 5. Upsert on change — latest-dated transaction wins via re-derivation
# ---------------------------------------------------------------------------


class TestUpsertOnChange:
    def test_extending_the_chain_updates_closing(self) -> None:
        cb1 = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        with Store(":memory:") as store:
            store.add_new(_result(cb1))
            store.record_month_balances(["2026-06"])
            assert _stored(store, "commbank", "2026-06") == "90.00"

            # A later-dated transaction extends the chain: 90 + (-20) = 70.
            cb2 = _txn("SYNTH SHOP B", amount="-20.00", d=date(2026, 6, 2), balance="70.00")
            store.add_new(_result(cb2))
            changed = store.record_month_balances(["2026-06"])

            assert changed == 1
            assert _stored(store, "commbank", "2026-06") == "70.00"


# ---------------------------------------------------------------------------
# 6. balance_series — contiguous axis, gaps as None, net rule, str everywhere
# ---------------------------------------------------------------------------


class TestBalanceSeriesShape:
    def _seed(self, store: Store) -> None:
        # CommBank: 2026-04 (100.00) and 2026-06 (200.00); GAP at 2026-05.
        store.add_new(_result(
            _txn("SYNTH APR", amount="-10.00", d=date(2026, 4, 1), balance="100.00"),
        ))
        store.add_new(_result(
            _txn("SYNTH JUN", amount="-10.00", d=date(2026, 6, 1), balance="200.00"),
        ))
        # Westpac: only 2026-06 (50.00).
        store.add_new(_result(
            _txn("SYNTH WP", amount="-5.00", d=date(2026, 6, 2),
                 bank=Bank.WESTPAC, balance="50.00"),
        ))
        store.record_month_balances(["2026-04", "2026-05", "2026-06"])

    def test_months_are_contiguous_range(self) -> None:
        with Store(":memory:") as store:
            self._seed(store)
            series = store.balance_series()
            assert series["months"] == ["2026-04", "2026-05", "2026-06"]

    def test_series_order_and_per_bank_gaps_are_none(self) -> None:
        with Store(":memory:") as store:
            self._seed(store)
            series = store.balance_series()

            banks = [s["bank"] for s in series["series"]]
            assert banks == ["commbank", "westpac"]

            cb = next(s for s in series["series"] if s["bank"] == "commbank")
            wp = next(s for s in series["series"] if s["bank"] == "westpac")
            assert cb["values"] == ["100.00", None, "200.00"]
            assert wp["values"] == [None, None, "50.00"]

    def test_net_is_none_wherever_any_bank_is_none(self) -> None:
        with Store(":memory:") as store:
            self._seed(store)
            series = store.balance_series()
            # 2026-04: westpac missing -> None; 2026-05: both missing -> None;
            # 2026-06: 200.00 + 50.00 -> 250.00.
            assert series["net"] == [None, None, "250.00"]

    def test_every_money_value_is_str_and_roundtrips_never_float(self) -> None:
        with Store(":memory:") as store:
            self._seed(store)
            series = store.balance_series()

            for m in series["months"]:
                assert isinstance(m, str)
            for s in series["series"]:
                for v in s["values"]:
                    assert v is None or isinstance(v, str)
                    assert not isinstance(v, float)
                    if v is not None:
                        assert amount_from_text(v) == Decimal(v)
            for v in series["net"]:
                assert v is None or isinstance(v, str)
                assert not isinstance(v, float)
                if v is not None:
                    assert amount_from_text(v) == Decimal(v)


# ---------------------------------------------------------------------------
# 7. Empty DB
# ---------------------------------------------------------------------------


class TestBalanceSeriesEmpty:
    def test_empty_db_exact_shape(self) -> None:
        with Store(":memory:") as store:
            assert store.balance_series() == {"months": [], "series": [], "net": []}


# ---------------------------------------------------------------------------
# 8. reset_all_data clears balances and reports the count
# ---------------------------------------------------------------------------


class TestResetClearsBalances:
    def test_reset_reports_and_wipes_balances(self) -> None:
        cb = _txn("SYNTH SHOP A", amount="-10.00", d=date(2026, 6, 1), balance="90.00")
        wp = _txn("SYNTH BILL", amount="-5.00", d=date(2026, 6, 2),
                  bank=Bank.WESTPAC, balance="50.00")
        with Store(":memory:") as store:
            store.add_new(_result(cb, wp))
            store.record_month_balances(["2026-06"])
            assert _count(store) == 2

            counts = store.reset_all_data()

            assert counts["balances"] == 2
            assert _count(store) == 0


# ---------------------------------------------------------------------------
# 9. Month-key validation — a garbage key is skipped, never raises
# ---------------------------------------------------------------------------


class TestMonthKeyValidation:
    def test_garbage_month_key_is_a_safe_noop(self) -> None:
        with Store(":memory:") as store:
            written = store.record_month_balances(["garbage", "2026/06", ""])
            assert written == 0
            assert _count(store) == 0
