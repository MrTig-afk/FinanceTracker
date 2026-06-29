"""test_idempotency.py — pytest suite for the §7.4 idempotency stage.

ALL fixtures use synthetic data generated inline.
No real transactions, no real descriptions, no real account numbers.
Never reads data/inbox/* or any tracked CSV file.
No network calls anywhere in this file.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from backend.data_source import Bank, Transaction
from backend.idempotency import (
    NewTxnResult,
    file_fingerprint,
    file_fingerprint_text,
    filter_new_transactions,
    is_file_seen,
    is_noop,
    select_uncategorised,
    transaction_fingerprint,
)

# ---------------------------------------------------------------------------
# Synthetic helpers — invented values; never real data
# ---------------------------------------------------------------------------

_SYNTH_DATE = date(2025, 3, 10)  # fixed synthetic date; not meaningful


def _txn(
    desc: str = "MERCHANT A",
    amount: str = "-10.00",
    d: date = _SYNTH_DATE,
    bank: Bank = Bank.COMMBANK,
) -> Transaction:
    """Build a synthetic Transaction without touching any real data."""
    return Transaction(date=d, description=desc, amount=Decimal(amount), bank=bank)


# ---------------------------------------------------------------------------
# TestFileFingerprint
# ---------------------------------------------------------------------------


class TestFileFingerprint:
    def test_deterministic_same_bytes(self) -> None:
        data = b"synthetic,csv,content,row\n1,2,3,4\n"
        assert file_fingerprint(data) == file_fingerprint(data)

    def test_one_byte_changed_differs(self) -> None:
        data = b"synthetic,csv,content,row\n1,2,3,4\n"
        data_changed = b"synthetic,csv,content,row\n1,2,3,5\n"
        assert file_fingerprint(data) != file_fingerprint(data_changed)

    def test_text_convenience_matches_bytes(self) -> None:
        text = "synthetic,csv,content,row\n1,2,3,4\n"
        assert file_fingerprint_text(text) == file_fingerprint(text.encode("utf-8"))

    def test_returns_64_char_lowercase_hex(self) -> None:
        fp = file_fingerprint(b"some synthetic bytes")
        assert len(fp) == 64
        assert fp == fp.lower()
        assert all(c in "0123456789abcdef" for c in fp)

    def test_is_file_seen_true_when_in_set(self) -> None:
        fp = file_fingerprint(b"synthetic data abc")
        assert is_file_seen(fp, {fp, "other-fp"}) is True

    def test_is_file_seen_false_when_absent(self) -> None:
        fp = file_fingerprint(b"synthetic data abc")
        assert is_file_seen(fp, {"different-fp"}) is False

    def test_is_file_seen_empty_set(self) -> None:
        fp = file_fingerprint(b"anything synthetic")
        assert is_file_seen(fp, set()) is False


# ---------------------------------------------------------------------------
# TestTransactionFingerprint
# ---------------------------------------------------------------------------


class TestTransactionFingerprint:
    def test_deterministic_same_txn(self) -> None:
        txn = _txn()
        assert transaction_fingerprint(txn) == transaction_fingerprint(txn)

    def test_decimal_trailing_zero_same_fp(self) -> None:
        """Decimal('5.5') and Decimal('5.50') must produce the same fingerprint."""
        t1 = _txn(amount="5.5")
        t2 = _txn(amount="5.50")
        assert transaction_fingerprint(t1) == transaction_fingerprint(t2)

    def test_negative_zero_same_as_zero(self) -> None:
        """Decimal('-0.00') and Decimal('0.00') must produce the same fingerprint."""
        t1 = _txn(amount="-0.00")
        t2 = _txn(amount="0.00")
        assert transaction_fingerprint(t1) == transaction_fingerprint(t2)

    def test_whitespace_case_variants_equal(self) -> None:
        """Whitespace collapse and uppercasing: tabs, double-spaces, leading/trailing spaces."""
        t1 = _txn(desc="SYNTH STORE XYZ")
        t2 = _txn(desc="synth  store\txyz")      # double space and tab
        t3 = _txn(desc="  SYNTH STORE XYZ  ")    # leading/trailing spaces
        fp1 = transaction_fingerprint(t1)
        assert transaction_fingerprint(t2) == fp1
        assert transaction_fingerprint(t3) == fp1

    def test_different_description_differs(self) -> None:
        """Descriptions that are genuinely different (after normalisation) hash differently."""
        t1 = _txn(desc="SYNTH SHOP ALPHA")
        t2 = _txn(desc="SYNTH SHOP BETA")
        assert transaction_fingerprint(t1) != transaction_fingerprint(t2)

    def test_different_date_differs(self) -> None:
        t1 = _txn(d=date(2025, 1, 1))
        t2 = _txn(d=date(2025, 1, 2))
        assert transaction_fingerprint(t1) != transaction_fingerprint(t2)

    def test_different_amount_differs(self) -> None:
        t1 = _txn(amount="-10.00")
        t2 = _txn(amount="-20.00")
        assert transaction_fingerprint(t1) != transaction_fingerprint(t2)

    def test_different_sign_differs(self) -> None:
        t1 = _txn(amount="-10.00")
        t2 = _txn(amount="10.00")
        assert transaction_fingerprint(t1) != transaction_fingerprint(t2)

    def test_different_bank_differs(self) -> None:
        t1 = _txn(bank=Bank.COMMBANK)
        t2 = _txn(bank=Bank.WESTPAC)
        assert transaction_fingerprint(t1) != transaction_fingerprint(t2)

    def test_returns_64_char_lowercase_hex(self) -> None:
        fp = transaction_fingerprint(_txn())
        assert len(fp) == 64
        assert fp == fp.lower()
        assert all(c in "0123456789abcdef" for c in fp)


# ---------------------------------------------------------------------------
# TestFilterNewTransactions
# ---------------------------------------------------------------------------


class TestFilterNewTransactions:
    def test_empty_input_returns_empty_result(self) -> None:
        result = filter_new_transactions([], set())
        assert result == NewTxnResult((), (), 0)
        assert is_noop(result) is True

    def test_all_new_empty_seen(self) -> None:
        txns = [_txn("STORE A"), _txn("STORE B"), _txn("STORE C")]
        result = filter_new_transactions(txns, set())
        assert len(result.new_transactions) == 3
        assert len(result.fingerprints) == 3
        assert result.duplicates_in_batch == 0
        # Parallel invariant
        for txn, fp in zip(result.new_transactions, result.fingerprints):
            assert fp == transaction_fingerprint(txn)

    def test_within_batch_duplicate_collapsed(self) -> None:
        """Two byte-identical rows → first kept, duplicates_in_batch == 1."""
        txn = _txn("SAME MERCHANT")
        result = filter_new_transactions([txn, txn], set())
        assert len(result.new_transactions) == 1
        assert result.duplicates_in_batch == 1

    def test_overlapping_batches(self) -> None:
        """Rows already in seen_fps are skipped even if they appear in the new batch."""
        shared = _txn("SHARED MERCHANT", amount="-5.00")
        only_new = _txn("NEW MERCHANT", amount="-7.00")
        batch1 = [shared]
        result1 = filter_new_transactions(batch1, set())
        seen_after_batch1 = set(result1.fingerprints)

        batch2 = [shared, only_new]   # shared reappears in second export
        result2 = filter_new_transactions(batch2, seen_after_batch1)
        assert len(result2.new_transactions) == 1
        assert result2.new_transactions[0] == only_new

    def test_order_of_first_occurrence_preserved(self) -> None:
        t1 = _txn("ALPHA SYNTH")
        t2 = _txn("BETA SYNTH")
        t3 = _txn("GAMMA SYNTH")
        result = filter_new_transactions([t1, t2, t3], set())
        assert list(result.new_transactions) == [t1, t2, t3]

    def test_seen_fps_as_list_still_works(self) -> None:
        """seen_fps may be passed as a list; dedupe must still work and be correct."""
        txn = _txn("MERCHANT LIST TEST")
        fp = transaction_fingerprint(txn)
        seen_as_list = [fp]  # list, not set
        result = filter_new_transactions([txn], seen_as_list)
        assert result.new_transactions == ()
        assert is_noop(result) is True


# ---------------------------------------------------------------------------
# TestSelectUncategorised
# ---------------------------------------------------------------------------


class TestSelectUncategorised:
    def test_empty_categorised_returns_all(self) -> None:
        txns = [_txn("SYNTH X"), _txn("SYNTH Y")]
        result = select_uncategorised(txns, set())
        assert result == txns

    def test_all_categorised_returns_empty(self) -> None:
        txns = [_txn("SYNTH X"), _txn("SYNTH Y")]
        fps = {transaction_fingerprint(t) for t in txns}
        result = select_uncategorised(txns, fps)
        assert result == []

    def test_partial_categorised_filters_correctly(self) -> None:
        t1 = _txn("SYNTH CAT")
        t2 = _txn("SYNTH UNCAT")
        fp1 = transaction_fingerprint(t1)
        result = select_uncategorised([t1, t2], {fp1})
        assert result == [t2]

    def test_order_preserved(self) -> None:
        txns = [_txn("ALPHA"), _txn("BETA"), _txn("GAMMA")]
        # categorise only the middle one
        mid_fp = transaction_fingerprint(txns[1])
        result = select_uncategorised(txns, {mid_fp})
        assert result == [txns[0], txns[2]]

    def test_categorised_fps_as_list_works(self) -> None:
        txn = _txn("SYNTH LIST CHECK")
        fp = transaction_fingerprint(txn)
        result = select_uncategorised([txn], [fp])  # list, not set
        assert result == []


# ---------------------------------------------------------------------------
# TestNoOpReRun — MANDATORY (FR-15 / pre-deployment idempotency check)
# ---------------------------------------------------------------------------


class TestNoOpReRun:
    def test_rerun_on_unchanged_input_is_noop(self) -> None:
        """FR-15 proof: a re-run on the same batch changes nothing.

        Ingest a synthetic batch once, collect fingerprints.  Run
        filter_new_transactions again with those fingerprints as seen → empty result.
        select_uncategorised on the empty result with an empty categorised set also
        returns [] → zero rows to send to the LLM.
        """
        batch = [
            _txn("SYNTH MERCHANT ONE", amount="-15.00"),
            _txn("SYNTH MERCHANT TWO", amount="-30.00", d=date(2025, 4, 1)),
            _txn("SYNTH MERCHANT THREE", amount="200.00", bank=Bank.WESTPAC),
        ]

        # First ingest — all are new
        first_result = filter_new_transactions(batch, set())
        assert len(first_result.new_transactions) == 3
        assert is_noop(first_result) is False

        # Simulate persisting the fingerprints (what the store will do in §7.7)
        persisted_fps = set(first_result.fingerprints)

        # Re-run on the SAME batch — must be a complete no-op
        second_result = filter_new_transactions(batch, persisted_fps)
        assert second_result.new_transactions == ()
        assert is_noop(second_result) is True

        # Contract: nothing to ingest → nothing to categorise → zero LLM rows
        to_categorise = select_uncategorised(list(second_result.new_transactions), set())
        assert to_categorise == []
        # ^ This proves FR-15: a re-run on unchanged input is a no-op end-to-end.
