"""test_pipeline.py — pytest suite for backend/pipeline.py (§7.2).

ALL fixtures use SYNTHETIC data generated in code.
No real transactions, no real account numbers, no real CSV files read from disk.
No live network calls — analyser client is a FakeAnalyserClient.
Drive unconfigured (drive_service=None) in every test.
DB: :memory: — NEVER ./data/ or any tracked path.
Outputs (xlsx) and sanitiser audit logs: tmp_path only.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from backend.data_source import Bank
from backend.pipeline import UploadedFile, run_pipeline
from backend.store import Store


# ---------------------------------------------------------------------------
# Synthetic CSV text — invented merchants; never real data
# ---------------------------------------------------------------------------

# CommBank: no header, DD/MM/YYYY, signed amount, description, balance
_CB_TEXT = (
    "20/06/2026,-72.40,WOOLWORTHS METRO,1000.00\n"
    "21/06/2026,-18.90,SYNTH TRANSPORT CO,927.60\n"
    "22/06/2026,-9.50,SYNTH COFFEE SHOP,918.10\n"
)
_CB_BYTES = _CB_TEXT.encode("utf-8")

# Westpac: header row; col-0 = account number (dropped by parser); split debit/credit
_WP_TEXT = (
    "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial\n"
    "748007654321,23/06/2026,SYNTH UTILITY BILL,130.05,,2000.00,,\n"
    "748007654321,24/06/2026,SYNTH SALARY CREDIT,,3200.00,5200.00,,\n"
)
_WP_BYTES = _WP_TEXT.encode("utf-8")

# Synthetic account number used in Westpac CSV — must never appear in stored descriptions
_FAKE_ACCT = "748007654321"


def _make_uploads() -> list[UploadedFile]:
    """Two synthetic bank files: one CommBank, one Westpac."""
    return [
        UploadedFile(filename="commbank.csv", bank=Bank.COMMBANK, content=_CB_BYTES),
        UploadedFile(filename="westpac.csv", bank=Bank.WESTPAC, content=_WP_BYTES),
    ]


# ---------------------------------------------------------------------------
# Fake analyser client — records calls, returns canned categories. Zero network.
# ---------------------------------------------------------------------------

class FakeAnalyserClient:
    """Minimal stand-in for OpenRouterClient.

    Parses the user_prompt JSON to determine how many rows to categorise, then
    assigns `default_category` to every row.  Tracks call_count and received
    user_prompts so tests can assert zero-call idempotency and privacy invariants.
    """

    def __init__(self, default_category: str = "Groceries") -> None:
        self.call_count = 0
        self.received_user_prompts: list[str] = []
        self._default_category = default_category

    def complete(self, *, system_prompt: str, user_prompt: str) -> tuple[dict, str]:
        self.call_count += 1
        self.received_user_prompts.append(user_prompt)
        items = json.loads(user_prompt)
        categories = {str(item["row_index"]): self._default_category for item in items}
        return (
            {
                "categories": categories,
                "summary": "Synthetic test summary.",
                "flagged": [],
            },
            "fake-model",
        )


# ---------------------------------------------------------------------------
# TestEndToEndHappyPath
# ---------------------------------------------------------------------------

class TestEndToEndHappyPath:
    """Full pipeline: CommBank + Westpac synthetic CSVs, fake analyser, Drive off."""

    @pytest.fixture(autouse=True)
    def _run(self, tmp_path):
        self.fake = FakeAnalyserClient()
        self.store = Store(":memory:")
        self.tmp_path = tmp_path
        self.report = run_pipeline(
            _make_uploads(),
            store=self.store,
            analyser_client=self.fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        yield
        self.store.close()

    def test_noop_is_false(self):
        assert self.report.noop is False

    def test_new_txns_count(self):
        # 3 CommBank + 2 Westpac = 5
        assert self.report.new_txns == 5

    def test_categorised_equals_new_txns(self):
        assert self.report.categorised == self.report.new_txns

    def test_excel_path_is_a_file_in_tmp(self):
        import pathlib
        assert self.report.excel_path is not None
        path = pathlib.Path(self.report.excel_path)
        assert path.exists(), f"Excel workbook not found at {self.report.excel_path}"
        assert path.is_relative_to(self.tmp_path) or str(self.tmp_path) in str(path)

    def test_drive_file_id_is_none(self):
        """Drive unconfigured → drive_file_id is None."""
        assert self.report.drive_file_id is None

    def test_model_used_is_fake_model(self):
        assert self.report.model_used == "fake-model"

    def test_files_seen_is_two(self):
        assert self.report.files_seen == 2

    def test_files_skipped_is_zero(self):
        assert self.report.files_skipped == 0

    def test_errors_empty(self):
        assert self.report.errors == []

    def test_year_month_is_june_2026(self):
        assert self.report.year_month == "2026-06"

    def test_summary_has_groceries(self):
        summary = self.store.summary("2026-06")
        assert "Groceries" in summary["totals"]

    def test_summary_count_is_five(self):
        summary = self.store.summary("2026-06")
        assert summary["count"] == 5

    # --- Parser-specific assertions (FR-8, FR-9) ---

    def test_westpac_debit_stored_as_negative(self):
        """Westpac debit column → negative amount (FR-9 merge rule)."""
        from decimal import Decimal
        rows = self.store.transactions_for_month("2026-06")
        row = next((r for r in rows if "SYNTH UTILITY BILL" in r.description), None)
        assert row is not None, "SYNTH UTILITY BILL not found in store"
        assert row.amount < Decimal("0"), f"Debit must be negative, got {row.amount}"

    def test_westpac_credit_stored_as_positive(self):
        """Westpac credit column → positive amount (FR-9 merge rule)."""
        from decimal import Decimal
        rows = self.store.transactions_for_month("2026-06")
        row = next((r for r in rows if "SYNTH SALARY CREDIT" in r.description), None)
        assert row is not None, "SYNTH SALARY CREDIT not found in store"
        assert row.amount > Decimal("0"), f"Credit must be positive, got {row.amount}"

    def test_commbank_signed_amount_correct(self):
        """CommBank amount is already signed; debit is negative (FR-8)."""
        from decimal import Decimal
        rows = self.store.transactions_for_month("2026-06")
        row = next((r for r in rows if "WOOLWORTHS METRO" in r.description), None)
        assert row is not None
        assert row.amount == Decimal("-72.40")

    def test_westpac_account_number_not_in_descriptions(self):
        """Westpac account-number column dropped; never stored in description (FR-9)."""
        rows = self.store.transactions_for_month("2026-06")
        for row in rows:
            assert _FAKE_ACCT not in row.description, (
                f"Account number leaked into description: {row.description!r}"
            )


# ---------------------------------------------------------------------------
# TestIdempotencyReUpload  (FR-15)
# ---------------------------------------------------------------------------

class TestIdempotencyReUpload:
    """Re-upload of identical bytes → noop, zero additional LLM calls."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.store = Store(":memory:")
        self.fake = FakeAnalyserClient()
        uploads = _make_uploads()

        self.first = run_pipeline(
            uploads,
            store=self.store,
            analyser_client=self.fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        self._calls_after_first = self.fake.call_count

        self.second = run_pipeline(
            uploads,
            store=self.store,
            analyser_client=self.fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        self._calls_after_second = self.fake.call_count
        yield
        self.store.close()

    def test_first_run_noop_false(self):
        assert self.first.noop is False

    def test_first_run_has_transactions(self):
        assert self.first.new_txns > 0

    def test_second_run_noop_true(self):
        assert self.second.noop is True

    def test_second_run_zero_new_txns(self):
        assert self.second.new_txns == 0

    def test_second_run_files_skipped_both(self):
        """Both files skipped at Layer 1 (file-fingerprint hit)."""
        assert self.second.files_skipped == 2

    def test_no_llm_calls_on_second_run(self):
        """FR-15: fake analyser receives ZERO additional calls on the second run."""
        additional = self._calls_after_second - self._calls_after_first
        assert additional == 0, (
            f"Expected 0 additional LLM calls on re-upload, got {additional}"
        )

    def test_second_run_excel_path_none(self):
        assert self.second.excel_path is None

    def test_second_run_drive_file_id_none(self):
        assert self.second.drive_file_id is None

    def test_second_run_model_used_empty(self):
        assert self.second.model_used == ""


# ---------------------------------------------------------------------------
# TestFileFingerPrintSkip  (Layer 1, FR-12)
# ---------------------------------------------------------------------------

class TestFileFingerPrintSkip:
    """Layer-1 file-fingerprint skip counts correctly."""

    def test_files_skipped_two_on_reupload(self, tmp_path):
        store = Store(":memory:")
        fake = FakeAnalyserClient()
        uploads = _make_uploads()
        run_pipeline(
            uploads,
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        second = run_pipeline(
            uploads,
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()
        assert second.files_skipped == 2

    def test_single_file_skipped_not_parsed_twice(self, tmp_path):
        """Re-upload of a single file → files_skipped == 1."""
        store = Store(":memory:")
        fake = FakeAnalyserClient()
        cb = UploadedFile("commbank.csv", Bank.COMMBANK, _CB_BYTES)
        run_pipeline(
            [cb],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        second = run_pipeline(
            [cb],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()
        assert second.files_skipped == 1


# ---------------------------------------------------------------------------
# TestFailClosedRow  (FR-21 sanitiser + privacy)
# ---------------------------------------------------------------------------

class TestFailClosedRow:
    """A description that scrubs to nothing safe → dropped by sanitiser, stored as
    'Other', never sent to the analyser (FR-21 fail-closed gate)."""

    # A bare email address is scrubbed to an empty string → the fail-closed gate
    # drops the row. (Note: a store number like "SHOP 1234" is NOT dropped under
    # the strip-all-digits policy — the digits are stripped and "SHOP" is kept.)
    _DROP_DESC = "leak@bad.test"
    _DROP_AMT = "-5.00"
    _GOOD_DESC = "WOOLWORTHS METRO"
    _GOOD_AMT = "-72.40"

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        csv_text = (
            f"20/06/2026,{self._DROP_AMT},{self._DROP_DESC},100.00\n"
            f"21/06/2026,{self._GOOD_AMT},{self._GOOD_DESC},1000.00\n"
        )
        self.store = Store(":memory:")
        self.fake = FakeAnalyserClient()
        self.report = run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, csv_text.encode())],
            store=self.store,
            analyser_client=self.fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        yield
        self.store.close()

    def test_both_rows_counted_as_new(self):
        """Dropped row still counts as a new transaction (it's stored, just as 'Other')."""
        assert self.report.new_txns == 2

    def test_dropped_row_stored_as_other(self):
        """Fail-closed row persisted with category 'Other' (not sent to LLM)."""
        rows = self.store.transactions_for_month("2026-06")
        dropped = next((r for r in rows if self._DROP_DESC in r.description), None)
        assert dropped is not None, "Fail-closed row must be present in store"
        assert dropped.category == "Other", (
            f"Fail-closed row must have category 'Other', got {dropped.category!r}"
        )

    def test_raw_drop_description_not_in_llm_payload(self):
        """The sanitiser drops the row; its raw description must NOT reach the fake client."""
        assert self.fake.call_count >= 1, "Fake analyser was never called"
        all_prompts = " ".join(self.fake.received_user_prompts)
        assert self._DROP_DESC not in all_prompts, (
            f"Fail-closed description {self._DROP_DESC!r} leaked into analyser payload"
        )
        # Verify the email local-part is also absent
        assert "leak" not in all_prompts

    def test_good_row_categorised(self):
        """Non-dropped row is categorised via the fake analyser."""
        rows = self.store.transactions_for_month("2026-06")
        good = next((r for r in rows if self._GOOD_DESC in r.description), None)
        assert good is not None
        assert good.category == "Groceries"

    def test_payload_keys_are_exactly_three(self):
        """BLOCKING: every item the analyser receives has only the three allowed keys."""
        for prompt_str in self.fake.received_user_prompts:
            items = json.loads(prompt_str)
            for item in items:
                assert set(item.keys()) == {"row_index", "cleaned_description", "amount"}, (
                    f"Unexpected keys in off-machine payload: {set(item.keys())}"
                )


# ---------------------------------------------------------------------------
# TestBadParse
# ---------------------------------------------------------------------------

class TestBadParse:
    """Parse failure for one file → safe error; good file still processed; no crash."""

    def test_safe_error_and_good_file_ingested(self, tmp_path, monkeypatch):
        import backend.pipeline as pipeline_mod
        from backend.data_source import parse_text as real_parse_text

        def selective_parse(text: str, bank):
            if bank == Bank.COMMBANK:
                raise RuntimeError("simulated parse failure (synthetic test only)")
            return real_parse_text(text, bank)

        monkeypatch.setattr(pipeline_mod, "parse_text", selective_parse)

        store = Store(":memory:")
        fake = FakeAnalyserClient()

        # run_pipeline must NOT raise
        report = run_pipeline(
            _make_uploads(),
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        # One safe error for the failed CommBank file
        assert len(report.errors) == 1

        err = report.errors[0]
        # Error must be a fixed safe string — no raw exception text, no CSV content
        assert "RuntimeError" not in err, "Raw exception class name must not appear"
        assert "simulated" not in err, "Raw exception message must not appear in error"
        assert _CB_TEXT[:20] not in err, "Raw CSV content must not appear in error"
        # Safe string contains the bank identifier
        assert "commbank" in err.lower()

        # Westpac file still processed (2 rows)
        assert report.new_txns == 2

    def test_no_exception_raised(self, tmp_path, monkeypatch):
        """run_pipeline never raises; errors are captured in RunReport.errors."""
        import backend.pipeline as pipeline_mod

        def always_raise(text: str, bank):
            raise RuntimeError("simulated parse failure (synthetic)")

        monkeypatch.setattr(pipeline_mod, "parse_text", always_raise)

        store = Store(":memory:")
        fake = FakeAnalyserClient()
        try:
            run_pipeline(
                _make_uploads(),
                store=store,
                analyser_client=fake,
                drive_service=None,
                output_dir=tmp_path,
                sanitise_log_dir=tmp_path,
            )
        except Exception as exc:
            pytest.fail(f"run_pipeline must not raise; raised {type(exc).__name__}: {exc}")
        finally:
            store.close()


# ---------------------------------------------------------------------------
# TestSanitiserContract  (BLOCKING — FR-16)
# ---------------------------------------------------------------------------

class TestSanitiserContract:
    """BLOCKING: only (row_index, cleaned_description, amount) can leave the machine."""

    def test_sanitised_txn_has_exactly_three_fields(self):
        from backend.sanitiser import SanitisedTxn
        field_names = {f.name for f in dataclasses.fields(SanitisedTxn)}
        assert field_names == {"row_index", "cleaned_description", "amount"}, (
            f"SanitisedTxn must have exactly 3 fields, got: {field_names}"
        )

    def test_no_date_field_in_sanitised_txn(self):
        from backend.sanitiser import SanitisedTxn
        from decimal import Decimal
        stxn = SanitisedTxn(row_index=0, cleaned_description="MERCHANT", amount=Decimal("10.00"))
        assert not hasattr(stxn, "date")

    def test_no_bank_field_in_sanitised_txn(self):
        from backend.sanitiser import SanitisedTxn
        from decimal import Decimal
        stxn = SanitisedTxn(row_index=0, cleaned_description="MERCHANT", amount=Decimal("10.00"))
        assert not hasattr(stxn, "bank")

    def test_no_account_number_field_in_sanitised_txn(self):
        from backend.sanitiser import SanitisedTxn
        from decimal import Decimal
        stxn = SanitisedTxn(row_index=0, cleaned_description="MERCHANT", amount=Decimal("10.00"))
        assert not hasattr(stxn, "account_number")
        assert not hasattr(stxn, "balance")


# ---------------------------------------------------------------------------
# TestEmptyUploads
# ---------------------------------------------------------------------------

class TestEmptyUploads:
    """Empty upload list → noop with zero LLM calls and no store writes."""

    def test_empty_uploads_noop(self, tmp_path):
        store = Store(":memory:")
        fake = FakeAnalyserClient()
        report = run_pipeline(
            [],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert report.noop is True
        assert report.new_txns == 0
        assert report.files_seen == 0
        assert report.excel_path is None
        assert fake.call_count == 0
