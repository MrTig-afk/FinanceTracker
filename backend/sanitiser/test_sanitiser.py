"""test_sanitiser.py — pytest suite for the §7.5 sanitiser.

ALL fixtures use synthetic data generated inline.
No real transactions, no real account numbers, no real names or descriptions.
Never reads data/inbox/* or any tracked CSV file.
No network calls anywhere in this file.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date
from decimal import Decimal

import pytest

from backend.data_source import Bank, Transaction
from backend.sanitiser import SanitisedTxn, SanitiseResult, sanitise

# Internal scrub helpers imported from the submodule for unit-level assertions.
# This is test code only — other pipeline stages must go through sanitise().
from backend.sanitiser.scrub import has_residual_identifier, scrub_description

# ---------------------------------------------------------------------------
# Synthetic helpers — invented values; never real data
# ---------------------------------------------------------------------------

_SYNTH_DATE = date(2025, 3, 10)  # fixed synthetic date; not meaningful


def _txn(
    description: str,
    amount: str = "-10.00",
    bank: Bank = Bank.COMMBANK,
) -> Transaction:
    """Build a synthetic Transaction without touching any real data."""
    return Transaction(
        date=_SYNTH_DATE,
        description=description,
        amount=Decimal(amount),
        bank=bank,
    )


# ---------------------------------------------------------------------------
# Contract tests: only (row_index, cleaned_description, amount) can leave
# ---------------------------------------------------------------------------


class TestSanitisedTxnContract:
    """FR-16: SanitisedTxn must expose exactly three named fields."""

    def test_sanitised_txn_has_exactly_three_fields(self):
        """SanitisedTxn exposes only row_index, cleaned_description, amount — no more."""
        field_names = {f.name for f in dataclasses.fields(SanitisedTxn)}
        assert field_names == {"row_index", "cleaned_description", "amount"}

    def test_sanitised_txn_has_no_date_attribute(self):
        """SanitisedTxn must not expose date — date is never sent off-machine."""
        stxn = SanitisedTxn(row_index=0, cleaned_description="MERCHANT", amount=Decimal("10.00"))
        assert not hasattr(stxn, "date")

    def test_sanitised_txn_has_no_bank_attribute(self):
        """SanitisedTxn must not expose bank — bank metadata never leaves the machine."""
        stxn = SanitisedTxn(row_index=0, cleaned_description="MERCHANT", amount=Decimal("10.00"))
        assert not hasattr(stxn, "bank")

    def test_sanitised_txn_has_no_balance_attribute(self):
        """SanitisedTxn must not expose balance."""
        stxn = SanitisedTxn(row_index=0, cleaned_description="MERCHANT", amount=Decimal("10.00"))
        assert not hasattr(stxn, "balance")

    def test_sanitise_result_fields(self):
        """SanitiseResult must have exactly: payload, dropped, run_id, timestamp."""
        field_names = {f.name for f in dataclasses.fields(SanitiseResult)}
        assert field_names == {"payload", "dropped", "run_id", "timestamp"}

    def test_sanitised_txn_is_frozen(self):
        """SanitisedTxn is a frozen dataclass — any mutation attempt must raise."""
        stxn = SanitisedTxn(row_index=0, cleaned_description="MERCHANT", amount=Decimal("10.00"))
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
            stxn.row_index = 99  # type: ignore[misc]

    def test_sanitise_result_is_frozen(self):
        """SanitiseResult is a frozen dataclass — any mutation attempt must raise."""
        result = sanitise([], audit=False)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
            result.payload = ()  # type: ignore[misc]

    def test_package_exports_only_three_names(self):
        """The sanitiser package __all__ exports exactly SanitisedTxn, SanitiseResult, sanitise."""
        import backend.sanitiser as pkg
        assert set(pkg.__all__) == {"SanitisedTxn", "SanitiseResult", "sanitise"}


# ---------------------------------------------------------------------------
# Privacy tests: private identifiers must not reach the payload or the audit log
# ---------------------------------------------------------------------------


class TestPrivacyNoLeakage:
    """Assert that synthetic account numbers and person names cannot appear in output."""

    # Invented strings — obviously fake; never real identifiers
    _FAKE_PERSON = "ZorblaxFoobar"   # invented name; must never appear in any output
    _FAKE_ACCT_REF = "ZX99887766"    # invented reference token; must never appear in output

    def _make_leaky_txn(self) -> Transaction:
        """A transaction whose RAW description embeds both synthetic secrets.

        The P2P name pattern removes the person-name span; the Ref: pattern
        removes the account-ref token — leaving only a neutral merchant label.
        """
        desc = (
            f"Transfer From {self._FAKE_PERSON} to PayID Phone Account "
            f"Ref: {self._FAKE_ACCT_REF}"
        )
        return _txn(desc, "-55.73")

    def test_secrets_absent_from_payload_descriptions(self, tmp_path):
        """Neither the fake person name nor the fake account ref appears in any payload item."""
        txns = [self._make_leaky_txn(), _txn("BAKERY CENTRAL", "-8.50")]
        result = sanitise(txns, audit=False)

        all_cleaned = " ".join(s.cleaned_description for s in result.payload)
        assert self._FAKE_PERSON not in all_cleaned, (
            f"Person name '{self._FAKE_PERSON}' leaked into payload"
        )
        assert self._FAKE_ACCT_REF not in all_cleaned, (
            f"Account ref '{self._FAKE_ACCT_REF}' leaked into payload"
        )

    def test_secrets_absent_from_audit_log(self, tmp_path):
        """Neither secret substring appears anywhere in the written JSONL audit record."""
        txns = [self._make_leaky_txn(), _txn("CORNER GROCER", "-12.00")]
        sanitise(txns, audit=True, log_dir=tmp_path)

        audit_file = tmp_path / "sanitiser-audit.jsonl"
        assert audit_file.exists(), "Audit log must be written when audit=True"
        log_text = audit_file.read_text(encoding="utf-8")

        assert self._FAKE_PERSON not in log_text, (
            f"Person name '{self._FAKE_PERSON}' found in audit log"
        )
        assert self._FAKE_ACCT_REF not in log_text, (
            f"Account ref '{self._FAKE_ACCT_REF}' found in audit log"
        )

    def test_dropped_row_text_absent_from_audit_log(self, tmp_path):
        """Dropped rows appear only as bare integers in the audit log — no text is written."""
        # A bare email address scrubs to empty → dropped
        txns = [_txn("someone@example.test"), _txn("MERCHANT STORE", "-20.00")]
        result = sanitise(txns, audit=True, log_dir=tmp_path)

        assert 0 in result.dropped, "Bare email address must be dropped"

        log_text = (tmp_path / "sanitiser-audit.jsonl").read_text(encoding="utf-8")
        # The raw description text must not appear in the log at all
        assert "someone" not in log_text
        assert "example.test" not in log_text


# ---------------------------------------------------------------------------
# Fail-closed tests (FR-21)
# ---------------------------------------------------------------------------


class TestFailClosed:
    """FR-21: un-sanitisable descriptions are dropped, never sent."""

    def test_bare_email_scrubs_to_empty_and_is_dropped(self):
        """A description that is only an email address scrubs to '' and must be dropped."""
        txns = [_txn("someone@example.test")]
        result = sanitise(txns, audit=False)

        assert 0 in result.dropped
        assert all(s.row_index != 0 for s in result.payload)

    def test_four_digit_store_number_stripped_row_kept(self):
        """A 4-digit store number is stripped; the row is KEPT with its readable name."""
        # Policy: strip the digits, keep the name — not drop the whole row.
        txns = [_txn("SHOP 1234")]
        result = sanitise(txns, audit=False)

        assert 0 not in result.dropped
        assert len(result.payload) == 1
        assert result.payload[0].cleaned_description == "SHOP"
        assert "1234" not in result.payload[0].cleaned_description

    def test_five_digit_store_number_stripped_row_kept(self):
        """A 5-digit store number is stripped; the row is KEPT with its name."""
        txns = [_txn("STORE 12345")]
        result = sanitise(txns, audit=False)

        assert 0 not in result.dropped
        assert result.payload[0].cleaned_description == "STORE"

    def test_short_number_also_stripped(self):
        """Policy: ANY digit run is stripped, including short 1-3 digit numbers."""
        txns = [_txn("AISLE 123")]
        result = sanitise(txns, audit=False)

        assert len(result.payload) == 1
        assert result.payload[0].cleaned_description == "AISLE"
        assert 0 not in result.dropped

    def test_number_only_description_dropped_as_empty(self):
        """A description that is ONLY digits scrubs to '' and is dropped (fail-closed)."""
        txns = [_txn("123456789")]
        result = sanitise(txns, audit=False)

        assert 0 in result.dropped
        assert len(result.payload) == 0

    def test_bare_at_sign_is_dropped(self):
        """Description with a bare '@' (no TLD — email scrubber misses it) → dropped via '@' check."""
        txns = [_txn("SHOP @ CORNER")]
        result = sanitise(txns, audit=False)

        assert 0 in result.dropped

    def test_has_residual_identifier_payto_name(self):
        """has_residual_identifier catches 'payto Jordan' as a transfer-name marker residue."""
        # Tests the belt-and-braces residual gate independently of scrub_description.
        assert has_residual_identifier("payto Jordan") is True

    def test_has_residual_identifier_payid_name_uppercase(self):
        """has_residual_identifier catches 'PAYID Jordan' (all-caps keyword + Capitalised name)."""
        assert has_residual_identifier("PAYID Jordan") is True

    def test_has_residual_identifier_empty_string(self):
        """has_residual_identifier('') → True (empty string must be dropped)."""
        assert has_residual_identifier("") is True

    def test_has_residual_identifier_whitespace_only(self):
        """has_residual_identifier('   ') → True (whitespace-only must be dropped)."""
        assert has_residual_identifier("   ") is True

    def test_payload_and_dropped_are_disjoint(self):
        """payload row_indexes and dropped indexes must be completely disjoint sets."""
        txns = [
            _txn("WOOLWORTHS METRO", "-42.00"),   # idx 0 — passes
            _txn("leak@bad.test", "-5.00"),        # idx 1 — dropped (bare email → empty)
            _txn("CORNER BAKERY", "-8.50"),        # idx 2 — passes
        ]
        result = sanitise(txns, audit=False)

        payload_indexes = {s.row_index for s in result.payload}
        dropped_indexes = set(result.dropped)

        assert payload_indexes.isdisjoint(dropped_indexes), (
            "payload and dropped must not share any index"
        )

    def test_all_indexes_within_range(self):
        """All row_indexes (payload + dropped) are within range(len(input))."""
        txns = [
            _txn("MERCHANT ALPHA", "-10.00"),
            _txn("leak@bad.test", "-5.00"),     # dropped (bare email)
            _txn("MERCHANT BETA", "-15.00"),
        ]
        result = sanitise(txns, audit=False)

        all_indexes = {s.row_index for s in result.payload} | set(result.dropped)
        assert all_indexes <= set(range(len(txns)))

    def test_no_rows_silently_lost(self):
        """Every input row appears in exactly one of payload or dropped — nothing is lost."""
        txns = [
            _txn("BAKERY FINE", "-8.00"),
            _txn("someone@example.test"),    # dropped
            _txn("FUEL STATION", "-65.00"),
        ]
        result = sanitise(txns, audit=False)

        all_idx = set(result.dropped) | {s.row_index for s in result.payload}
        assert len(all_idx) == len(txns), "Every input row must appear in payload or dropped"

    def test_middle_row_dropped_indexes_map_to_original_positions(self):
        """When the middle row is dropped, surrounding rows keep their original input positions."""
        txns = [
            _txn("ALPHA MERCHANT", "-10.00"),   # idx 0 — passes
            _txn("leak@bad.test", "-5.00"),      # idx 1 — dropped (bare email)
            _txn("GAMMA MERCHANT", "-15.00"),    # idx 2 — passes
        ]
        result = sanitise(txns, audit=False)

        assert 1 in result.dropped
        payload_indexes = {s.row_index for s in result.payload}
        assert payload_indexes == {0, 2}

        # Verify each row_index maps back to its correct original description
        alpha = next(s for s in result.payload if s.row_index == 0)
        assert alpha.cleaned_description == "ALPHA MERCHANT"

        gamma = next(s for s in result.payload if s.row_index == 2)
        assert gamma.cleaned_description == "GAMMA MERCHANT"


# ---------------------------------------------------------------------------
# Scrub-class tests — one per identifier class, synthetic strings only
# ---------------------------------------------------------------------------


class TestScrubClasses:
    """One test per identifier class. All strings are invented synthetic values."""

    def test_partial_card_with_card_prefix_removed(self):
        """'Card xx1234 GROCERIES' → partial card marker removed; merchant text preserved."""
        cleaned = scrub_description("Card xx1234 GROCERIES")
        assert "xx1234" not in cleaned
        assert "1234" not in cleaned
        assert "GROCERIES" in cleaned

    def test_standalone_partial_card_removed(self):
        """'xx9876 PAYMENT' → standalone xx-prefixed partial card removed."""
        cleaned = scrub_description("xx9876 PAYMENT")
        assert "xx9876" not in cleaned
        assert "PAYMENT" in cleaned

    def test_value_date_annotation_stripped(self):
        """'SUPERMARKET Value Date: 01/02/2025' → trailing value-date annotation removed."""
        cleaned = scrub_description("SUPERMARKET Value Date: 01/02/2025")
        assert "Value Date" not in cleaned
        assert "01/02/2025" not in cleaned
        assert "SUPERMARKET" in cleaned

    def test_p2p_person_name_removed_merchant_keyword_preserved(self):
        """P2P name span removed; neutral transfer keyword preserved for categorisation."""
        raw = "Fast Transfer From Jordan Example to PayID Phone Savings"
        cleaned = scrub_description(raw)

        assert "Jordan" not in cleaned, "'Jordan' must be scrubbed from P2P description"
        assert "Example" not in cleaned, "'Example' must be scrubbed from P2P description"
        # At least one neutral transfer context keyword must survive
        assert "Fast" in cleaned or "Transfer" in cleaned, (
            "A transfer keyword must survive for categorisation"
        )

    def test_p2p_row_passes_fail_closed_gate(self):
        """After P2P name scrub, 'Fast Transfer From Jordan Example ...' row is in payload."""
        txns = [_txn("Fast Transfer From Jordan Example to PayID Phone Savings", "-25.00")]
        result = sanitise(txns, audit=False)
        # The row must appear in payload (not dropped) after name removal
        assert len(result.payload) == 1
        assert "Jordan" not in result.payload[0].cleaned_description
        assert "Example" not in result.payload[0].cleaned_description

    def test_payid_phone_handle_removed(self):
        """PayID phone handle digits removed from description."""
        cleaned = scrub_description("PAYID 0412 345 678")
        assert "0412 345 678" not in cleaned
        assert "0412" not in cleaned

    def test_payid_email_handle_removed(self):
        """PayID email handle and '@' symbol removed from description."""
        cleaned = scrub_description("PAYID a@b.test")
        assert "a@b.test" not in cleaned
        assert "@" not in cleaned

    def test_bsb_code_removed(self):
        """BSB-like NNN-NNN pattern removed; surrounding text preserved."""
        cleaned = scrub_description("TRANSFER 123-456 ACME")
        assert "123-456" not in cleaned
        assert "TRANSFER" in cleaned
        assert "ACME" in cleaned

    def test_full_spaced_card_number_removed(self):
        """4×4 spaced card number removed; surrounding merchant text preserved."""
        cleaned = scrub_description("4111 1111 1111 1111 COLES")
        assert "4111 1111 1111 1111" not in cleaned
        assert "4111" not in cleaned
        assert "COLES" in cleaned

    def test_bare_16_digit_card_number_removed(self):
        """Bare 16-digit run removed; surrounding text preserved."""
        cleaned = scrub_description("1234567890123456 SHOP")
        assert "1234567890123456" not in cleaned
        assert "SHOP" in cleaned

    def test_long_mobile_reference_removed(self):
        """7-digit MOBILE reference (6+ digits) removed; keyword may remain."""
        cleaned = scrub_description("MOBILE 1136212")
        assert "1136212" not in cleaned

    def test_ref_label_and_alphanumeric_token_removed(self):
        """'Ref: ALPHANUMTOKEN' — both the label and the token are removed."""
        cleaned = scrub_description("PAYMENT Ref: ABC123456")
        assert "Ref:" not in cleaned
        assert "ABC123456" not in cleaned
        assert "PAYMENT" in cleaned

    def test_multispace_and_store_number_collapsed_row_kept(self):
        """'WOOLWORTHS    1234   ' → digits stripped, whitespace collapsed, row KEPT."""
        txns = [_txn("WOOLWORTHS    1234   ")]
        result = sanitise(txns, audit=False)

        assert 0 not in result.dropped
        assert len(result.payload) == 1
        assert result.payload[0].cleaned_description == "WOOLWORTHS"

    def test_multispace_raw_string_not_in_payload(self):
        """'WOOLWORTHS    1234   ' must not appear in payload with raw whitespace or digit run."""
        txns = [_txn("WOOLWORTHS    1234   ")]
        result = sanitise(txns, audit=False)

        # Must never be sent as-is with leading/trailing spaces or the digit run
        assert not any(
            "    " in s.cleaned_description or "1234" in s.cleaned_description
            for s in result.payload
        )


# ---------------------------------------------------------------------------
# Digit-stripping policy — strip ALL digits, keep the readable merchant name
# ---------------------------------------------------------------------------


class TestDigitStripping:
    """Policy: every digit run is stripped from the description; the readable name
    survives so the analyser can categorise it. No number of any length ever
    reaches the payload. This is what keeps Woolworths/Coles/Aldi (whose EFTPOS
    descriptions carry a store number) out of 'Other' and in 'Groceries'.
    """

    def test_woolworths_store_number_stripped_name_survives(self):
        """'WOOLWORTHS 1234 SYDNEY' → 'WOOLWORTHS SYDNEY' (row kept)."""
        txns = [_txn("WOOLWORTHS 1234 SYDNEY", "-82.45")]
        result = sanitise(txns, audit=False)

        assert len(result.payload) == 1
        assert result.payload[0].cleaned_description == "WOOLWORTHS SYDNEY"

    def test_coles_store_number_stripped(self):
        """'COLES 5678 MELBOURNE' → 'COLES MELBOURNE' (Westpac row kept)."""
        txns = [_txn("COLES 5678 MELBOURNE", "-64.30", bank=Bank.WESTPAC)]
        result = sanitise(txns, audit=False)

        assert result.payload[0].cleaned_description == "COLES MELBOURNE"

    def test_aldi_store_number_stripped(self):
        """'ALDI 4321' → 'ALDI' (row kept)."""
        txns = [_txn("ALDI 4321", "-30.00")]
        result = sanitise(txns, audit=False)

        assert result.payload[0].cleaned_description == "ALDI"

    def test_asian_grocer_with_store_number_survives(self):
        """An obvious grocer name survives with its store number stripped."""
        txns = [_txn("GREAT WALL ASIAN SUPERMARKET 1234", "-40.00")]
        result = sanitise(txns, audit=False)

        assert result.payload[0].cleaned_description == "GREAT WALL ASIAN SUPERMARKET"

    def test_digit_embedded_in_token_stripped(self):
        """Digits fused to letters are stripped too: 'STORE99' → 'STORE'."""
        assert scrub_description("STORE99") == "STORE"

    def test_scrub_strips_all_digits_keeps_letters(self):
        """scrub_description removes every digit run, keeping the letters."""
        assert scrub_description("WOOLWORTHS 1234 SYDNEY") == "WOOLWORTHS SYDNEY"
        assert scrub_description("CAFE 42") == "CAFE"
        assert scrub_description("FUEL 900123 HIGHWAY") == "FUEL HIGHWAY"

    def test_no_digit_ever_reaches_payload(self):
        """Property: across many synthetic descriptions carrying numbers, NO digit
        character appears in ANY payload item."""
        import re as _re

        txns = [
            _txn("WOOLWORTHS 1234 SYDNEY"),
            _txn("COLES 5678"),
            _txn("ALDI 4321 STORE"),
            _txn("BAKERY 7 CENTRAL"),
            _txn("CAFE 42"),
            _txn("FUEL 900123 HIGHWAY"),
            _txn("MYKI TOPUP 55"),
        ]
        result = sanitise(txns, audit=False)

        for s in result.payload:
            assert not _re.search(r"\d", s.cleaned_description), (
                f"digit leaked into payload: {s.cleaned_description!r}"
            )


# ---------------------------------------------------------------------------
# Happy-path and contract tests
# ---------------------------------------------------------------------------


class TestHappyPath:
    """Happy-path, immutability, audit-log schema, and idempotency tests."""

    def test_clean_merchant_passes_index_zero(self):
        """A clean merchant description passes through at index 0 unchanged."""
        txns = [_txn("WOOLWORTHS METRO", "-42.75")]
        result = sanitise(txns, audit=False)

        assert len(result.payload) == 1
        assert result.payload[0].row_index == 0
        assert result.payload[0].cleaned_description == "WOOLWORTHS METRO"

    def test_amount_preserved_as_exact_decimal(self):
        """amount is preserved as the exact same Decimal value — not rounded or altered."""
        amount = Decimal("-42.75")
        txns = [_txn("WOOLWORTHS METRO", str(amount))]
        result = sanitise(txns, audit=False)

        assert result.payload[0].amount == amount

    def test_amount_type_is_decimal_not_float(self):
        """amount in payload is exactly Decimal — never float or str."""
        txns = [_txn("CORNER BAKERY", "-8.99")]
        result = sanitise(txns, audit=False)

        assert type(result.payload[0].amount) is Decimal
        assert not isinstance(result.payload[0].amount, float)

    def test_amount_decimal_precision_preserved(self):
        """A high-precision Decimal amount round-trips without loss."""
        amount = Decimal("-1234.56")
        txns = [_txn("FUEL STATION", str(amount))]
        result = sanitise(txns, audit=False)

        assert result.payload[0].amount == amount
        assert str(result.payload[0].amount) == str(amount)

    def test_input_list_not_mutated(self):
        """sanitise() must not mutate the input list or the Transaction objects (FR-19)."""
        txns = [
            _txn("WOOLWORTHS METRO", "-42.75"),
            _txn("CORNER BAKERY", "-8.99"),
        ]
        original_ids = [id(t) for t in txns]
        original_len = len(txns)
        original_descs = [t.description for t in txns]

        sanitise(txns, audit=False)

        assert len(txns) == original_len
        assert [id(t) for t in txns] == original_ids
        assert [t.description for t in txns] == original_descs

    def test_empty_input_returns_empty_payload_and_dropped(self):
        """sanitise([]) → payload == (), dropped == (), no exception raised."""
        result = sanitise([], audit=False)

        assert result.payload == ()
        assert result.dropped == ()

    def test_empty_input_no_exception(self):
        """sanitise([]) completes without raising any exception."""
        sanitise([], audit=False)  # must not raise

    def test_payload_is_tuple(self):
        """SanitiseResult.payload is an immutable tuple."""
        txns = [_txn("WOOLWORTHS METRO", "-10.00")]
        result = sanitise(txns, audit=False)
        assert isinstance(result.payload, tuple)

    def test_dropped_is_tuple(self):
        """SanitiseResult.dropped is an immutable tuple."""
        result = sanitise([], audit=False)
        assert isinstance(result.dropped, tuple)

    def test_audit_false_writes_no_file(self, tmp_path):
        """audit=False must not create any file even when log_dir is given."""
        txns = [_txn("WOOLWORTHS METRO", "-10.00")]
        sanitise(txns, audit=False, log_dir=tmp_path)

        assert not (tmp_path / "sanitiser-audit.jsonl").exists()

    def test_audit_true_writes_jsonl_file(self, tmp_path):
        """audit=True writes sanitiser-audit.jsonl to log_dir."""
        txns = [_txn("WOOLWORTHS METRO", "-10.00")]
        sanitise(txns, audit=True, log_dir=tmp_path)

        assert (tmp_path / "sanitiser-audit.jsonl").exists()

    def test_audit_log_amount_is_string_not_float(self, tmp_path):
        """Amount in the JSONL audit record is a string — never a float (Decimal exactness)."""
        txns = [_txn("WOOLWORTHS METRO", "-42.75")]
        sanitise(txns, audit=True, log_dir=tmp_path)

        log_text = (tmp_path / "sanitiser-audit.jsonl").read_text(encoding="utf-8")
        record = json.loads(log_text.strip().splitlines()[0])
        amount_val = record["payload"][0]["amount"]

        assert isinstance(amount_val, str), (
            f"amount in audit log must be str, got {type(amount_val).__name__}"
        )
        assert amount_val == "-42.75"

    def test_audit_log_has_exactly_allowed_top_level_keys(self, tmp_path):
        """JSONL record contains exactly the six allowed top-level keys — no extras."""
        txns = [_txn("CORNER BAKERY", "-8.50")]
        sanitise(txns, audit=True, log_dir=tmp_path)

        log_text = (tmp_path / "sanitiser-audit.jsonl").read_text(encoding="utf-8")
        record = json.loads(log_text.strip().splitlines()[0])

        expected_keys = {
            "run_id", "timestamp", "sent_count", "dropped_count",
            "payload", "dropped_row_index",
        }
        assert set(record.keys()) == expected_keys, (
            f"Unexpected audit keys: {set(record.keys()) - expected_keys}"
        )

    def test_run_id_matches_in_result_and_audit_log(self, tmp_path):
        """run_id on the returned SanitiseResult equals run_id in the written audit record."""
        txns = [_txn("FUEL STATION", "-65.00")]
        result = sanitise(txns, audit=True, log_dir=tmp_path)

        log_text = (tmp_path / "sanitiser-audit.jsonl").read_text(encoding="utf-8")
        record = json.loads(log_text.strip().splitlines()[0])

        assert result.run_id == record["run_id"]

    def test_result_has_nonempty_run_id_and_timestamp(self):
        """SanitiseResult always has a non-empty run_id and timestamp string."""
        result = sanitise([], audit=False)

        assert result.run_id
        assert result.timestamp
        assert isinstance(result.run_id, str)
        assert isinstance(result.timestamp, str)

    def test_multiple_clean_rows_sequential_indexes(self):
        """Multiple clean rows pass through with correctly sequential row_indexes."""
        txns = [
            _txn("MERCHANT ALPHA", "-10.00"),
            _txn("MERCHANT BETA", "-20.00"),
            _txn("MERCHANT GAMMA", "-30.00"),
        ]
        result = sanitise(txns, audit=False)

        assert len(result.payload) == 3
        assert result.payload[0].row_index == 0
        assert result.payload[1].row_index == 1
        assert result.payload[2].row_index == 2

    def test_westpac_transaction_passes(self):
        """A Westpac Transaction (different Bank enum) is also sanitised correctly."""
        txns = [_txn("COLES SUPERMARKET", "-55.00", bank=Bank.WESTPAC)]
        result = sanitise(txns, audit=False)

        assert len(result.payload) == 1
        assert result.payload[0].cleaned_description == "COLES SUPERMARKET"
        # Bank metadata must not leak into the SanitisedTxn
        assert not hasattr(result.payload[0], "bank")

    def test_sanitise_is_idempotent_on_same_input(self):
        """Re-running sanitise on the same input produces identical payload content (FR-19)."""
        txns = [
            _txn("WOOLWORTHS METRO", "-42.75"),
            _txn("leak@bad.test", "-5.00"),   # will be dropped (bare email)
            _txn("CORNER BAKERY", "-8.99"),
        ]
        result1 = sanitise(txns, audit=False)
        result2 = sanitise(txns, audit=False)

        # Payload content must be identical (run_id and timestamp are expected to differ)
        assert len(result1.payload) == len(result2.payload)
        for s1, s2 in zip(result1.payload, result2.payload):
            assert s1.row_index == s2.row_index
            assert s1.cleaned_description == s2.cleaned_description
            assert s1.amount == s2.amount

        assert result1.dropped == result2.dropped
