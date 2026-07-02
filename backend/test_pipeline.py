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

from datetime import date as _date
from decimal import Decimal

from backend.data_source import Bank, Transaction
from backend.idempotency import NewTxnResult, file_fingerprint, transaction_fingerprint
from backend.pipeline import UploadedFile, run_pipeline
from backend.sanitiser import sanitise
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
        self.received_system_prompts: list[str] = []
        self._default_category = default_category

    def complete(self, *, system_prompt: str, user_prompt: str) -> tuple[dict, str]:
        self.call_count += 1
        self.received_user_prompts.append(user_prompt)
        self.received_system_prompts.append(system_prompt)
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

    def test_single_file_skipped_counter_on_reupload(self, tmp_path):
        """Re-upload of a single already-processed file → files_skipped == 1.

        Note: the file IS still parsed on the second run (so reconcile_balances
        can see its rows) — files_skipped only means "not re-fingerprinted",
        not "not parsed". See TestReuploadBackfillsNullBalance for the case
        this behaviour exists to support.
        """
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


# ---------------------------------------------------------------------------
# TestAutoDetect — parser chosen by file CONTENT, not the upload slot
# ---------------------------------------------------------------------------

class TestAutoDetect:
    """A file is parsed by the profile matching its contents, whatever box it
    was dropped in. Unrecognised files are rejected safely and not fingerprinted."""

    def _run(self, uploads, store, tmp_path):
        return run_pipeline(
            uploads,
            store=store,
            analyser_client=FakeAnalyserClient(),
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )

    def test_westpac_content_in_commbank_slot_still_parses(self, tmp_path):
        store = Store(":memory:")
        uploads = [UploadedFile(filename="mislabeled.csv", bank=Bank.COMMBANK, content=_WP_BYTES)]
        report = self._run(uploads, store, tmp_path)
        # Detected as Westpac -> its 2 rows ingested despite the wrong slot.
        assert report.new_txns == 2
        assert report.errors == []
        rows = store.transactions_for_month()
        descs = " ".join(r.description for r in rows)
        # Westpac account-number column is still dropped after auto-detect.
        assert _FAKE_ACCT not in descs
        assert "SYNTH UTILITY BILL" in descs
        store.close()

    def test_commbank_content_in_westpac_slot_still_parses(self, tmp_path):
        store = Store(":memory:")
        uploads = [UploadedFile(filename="mislabeled.csv", bank=Bank.WESTPAC, content=_CB_BYTES)]
        report = self._run(uploads, store, tmp_path)
        assert report.new_txns == 3  # CommBank text has 3 rows
        assert report.errors == []
        store.close()

    def test_unrecognised_file_errors_and_is_not_fingerprinted(self, tmp_path):
        store = Store(":memory:")
        garbage = b"not,a,bank,export\njust,random,junk,here\n"

        def uploads():
            return [UploadedFile(filename="junk.csv", bank=Bank.COMMBANK, content=garbage)]

        r1 = self._run(uploads(), store, tmp_path)
        assert r1.noop is True
        assert any("unrecognised" in e.lower() for e in r1.errors)

        # Not fingerprinted: an identical re-upload is NOT skipped as processed.
        r2 = self._run(uploads(), store, tmp_path)
        assert r2.files_skipped == 0
        assert any("unrecognised" in e.lower() for e in r2.errors)
        store.close()


# ---------------------------------------------------------------------------
# TestCategoryContextPreamble — TAXONOMY & CONTEXT prepend, privacy-preserved
# ---------------------------------------------------------------------------

class TestCategoryContextPreamble:
    """With stored/seeded context, the fake analyser receives a system_prompt
    beginning with the TAXONOMY & CONTEXT preamble, and user_prompt is unaffected
    (still only row_index/cleaned_description/amount; no account number leak)."""

    def test_system_prompt_starts_with_taxonomy_and_context(self, tmp_path):
        store = Store(":memory:")
        fake = FakeAnalyserClient()
        run_pipeline(
            _make_uploads(),
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert fake.call_count >= 1
        for prompt in fake.received_system_prompts:
            assert prompt.startswith("TAXONOMY & CONTEXT")

    def test_stored_hints_appear_in_system_prompt(self, tmp_path):
        """A custom (SYNTHETIC) hint saved via the store shows up in the preamble."""
        store = Store(":memory:")
        store.save_category_context({"Groceries": "SYNTH CORNER STORE, SYNTH DELI"})
        fake = FakeAnalyserClient()
        run_pipeline(
            _make_uploads(),
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        all_prompts = " ".join(fake.received_system_prompts)
        assert "SYNTH CORNER STORE, SYNTH DELI" in all_prompts

    def test_user_prompt_still_only_three_keys_with_context(self, tmp_path):
        """BLOCKING: context preamble never leaks into or alters user_prompt shape."""
        store = Store(":memory:")
        fake = FakeAnalyserClient()
        run_pipeline(
            _make_uploads(),
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        for prompt_str in fake.received_user_prompts:
            for item in json.loads(prompt_str):
                assert set(item.keys()) == {"row_index", "cleaned_description", "amount"}

    def test_account_number_never_in_user_prompt_or_preamble(self, tmp_path):
        """BLOCKING: the synthetic account number never appears in the outgoing
        payload OR the preamble, even with context stored."""
        store = Store(":memory:")
        store.save_category_context({"Groceries": "SYNTH HINT MENTIONING NOTHING SECRET"})
        fake = FakeAnalyserClient()
        run_pipeline(
            _make_uploads(),
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        all_text = " ".join(fake.received_user_prompts) + " ".join(fake.received_system_prompts)
        assert _FAKE_ACCT not in all_text

    def test_idempotent_rerun_with_context_still_zero_llm_calls(self, tmp_path):
        """An unchanged re-run is still a no-op with zero LLM calls even when
        category context exists (idempotency unaffected by the preamble)."""
        store = Store(":memory:")
        store.save_category_context({"Groceries": "SYNTH HINT"})
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
        calls_after_first = fake.call_count

        second = run_pipeline(
            uploads,
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert second.noop is True
        assert fake.call_count == calls_after_first


# ---------------------------------------------------------------------------
# T4c — identical-bytes re-run: noop, balance_updates=0, zero LLM calls
# ---------------------------------------------------------------------------

class TestIdempotencyBalanceUpdatesField:
    """T4c: an identical-bytes re-run is a true no-op with RunReport.balance_updates == 0."""

    def test_identical_bytes_rerun_balance_updates_zero(self, tmp_path):
        store = Store(":memory:")
        fake = FakeAnalyserClient()
        uploads = _make_uploads()

        first = run_pipeline(
            uploads,
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        calls_after_first = fake.call_count

        second = run_pipeline(
            uploads,
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert first.balance_updates == 0
        assert second.noop is True
        assert second.balance_updates == 0
        assert fake.call_count == calls_after_first, "identical re-run must make zero LLM calls"


# ---------------------------------------------------------------------------
# T7 (pipeline-level) — PRIVACY (BLOCKING): balance never leaves the machine
# ---------------------------------------------------------------------------

class TestBalancePrivacyPipelineLevel:
    """T7: a distinctive balance sentinel never reaches the sanitised payload, the
    audit log, or the analyser — via both a sanitiser-unit check and a full
    pipeline run with a call-recording fake client."""

    _SENTINEL = Decimal("987654.32")

    def test_sanitiser_unit_balance_sentinel_never_leaves(self, tmp_path):
        txns = [
            Transaction(
                date=_date(2026, 6, 1),
                description="SYNTH BALANCE SHOP",
                amount=Decimal("-10.00"),
                bank=Bank.COMMBANK,
                balance=self._SENTINEL,
            )
        ]
        result = sanitise(txns, audit=True, log_dir=tmp_path)

        sentinel_str = str(self._SENTINEL)
        for stxn in result.payload:
            assert sentinel_str not in stxn.cleaned_description
            assert not hasattr(stxn, "balance")

        log_text = (tmp_path / "sanitiser-audit.jsonl").read_text(encoding="utf-8")
        assert sentinel_str not in log_text

    def test_pipeline_balance_sentinel_never_reaches_analyser(self, tmp_path):
        cb_text = f"20/06/2026,-72.40,SYNTH BALANCE SHOP,{self._SENTINEL}\n"
        store = Store(":memory:")
        fake = FakeAnalyserClient()
        run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, cb_text.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        sentinel_str = str(self._SENTINEL)
        all_text = " ".join(fake.received_user_prompts) + " ".join(fake.received_system_prompts)
        assert sentinel_str not in all_text
        assert fake.call_count >= 1, "fake analyser was never called"

    def test_sanitised_payload_unaffected_by_balance_field(self, tmp_path):
        """The off-machine payload is byte-for-byte the same whether or not a balance
        column is present — balance cannot reach the analyser via any path."""
        cb_with_balance = "20/06/2026,-72.40,SYNTH BALANCE SHOP,1000.00\n"
        cb_without_balance = "20/06/2026,-72.40,SYNTH BALANCE SHOP\n"  # short row -> balance None

        store_a = Store(":memory:")
        fake_a = FakeAnalyserClient()
        run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, cb_with_balance.encode())],
            store=store_a,
            analyser_client=fake_a,
            drive_service=None,
            output_dir=tmp_path / "a",
            sanitise_log_dir=tmp_path / "a",
        )
        store_a.close()

        store_b = Store(":memory:")
        fake_b = FakeAnalyserClient()
        run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, cb_without_balance.encode())],
            store=store_b,
            analyser_client=fake_b,
            drive_service=None,
            output_dir=tmp_path / "b",
            sanitise_log_dir=tmp_path / "b",
        )
        store_b.close()

        assert fake_a.received_user_prompts == fake_b.received_user_prompts


# ---------------------------------------------------------------------------
# T11 — Pipeline balance-corrected re-upload
# ---------------------------------------------------------------------------

class TestBalanceCorrectedReupload:
    """T11: a byte-different re-upload carrying a corrected balance updates the
    stored balance in place, with no duplicate row, zero analyser calls, and the
    category left unchanged."""

    _CB_TEXT_V1 = "20/06/2026,-72.40,SYNTH BALANCE SHOP,1000.00\n"
    _CB_TEXT_V2 = "20/06/2026,-72.40,SYNTH BALANCE SHOP,995.00\n"  # same txn, balance corrected

    def test_balance_corrected_reupload_updates_in_place(self, tmp_path):
        store = Store(":memory:")
        fake = FakeAnalyserClient()

        first = run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, self._CB_TEXT_V1.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        calls_after_first = fake.call_count
        assert calls_after_first >= 1

        second = run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, self._CB_TEXT_V2.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )

        rows = store.conn.execute("SELECT balance, category FROM transactions").fetchall()
        store.close()

        assert first.noop is False

        # Balance updated, no duplicate row.
        assert len(rows) == 1
        assert rows[0]["balance"] == "995.00"
        assert rows[0]["category"] == "Groceries"  # unchanged from the first run

        # Zero analyser calls on the balance-only re-upload.
        assert fake.call_count == calls_after_first

        # RunReport contract for a balance-only re-upload.
        assert second.noop is False
        assert second.new_txns == 0
        assert second.categorised == 0
        assert second.model_used == ""
        assert second.balance_updates == 1


# ---------------------------------------------------------------------------
# NEW — re-upload of an already-processed file whose stored balance is NULL
# (the realistic bug case: rows first ingested before balance capture existed,
# so the file is already fingerprinted but the balance column was never
# persisted). This is the case the Layer-1 restructure exists to fix: an
# already-processed file must still be parsed so reconcile_balances() can see
# its rows, not skipped-before-parse.
# ---------------------------------------------------------------------------

class TestReuploadBackfillsNullBalance:
    """A file already marked processed (fingerprinted), whose stored row has a
    NULL balance, backfills that balance when the same content is re-run
    through the pipeline — with no duplicate row, zero analyser calls, and the
    category left unchanged."""

    _CB_TEXT = "20/06/2026,-72.40,SYNTH BALANCE SHOP,1000.00\n"

    def _seed_legacy_row(self, store: Store) -> None:
        """Simulate a row ingested under the pre-balance-capture schema: the
        transaction is stored with balance=NULL and a category already set,
        and the file's exact bytes are already recorded as processed — even
        though (unlike the old code) parsing these bytes today WOULD yield a
        non-null balance, because at the time this row was first ingested the
        parser did not yet capture the balance column at all.
        """
        legacy_txn = Transaction(
            date=_date(2026, 6, 20),
            description="SYNTH BALANCE SHOP",
            amount=Decimal("-72.40"),
            bank=Bank.COMMBANK,
            balance=None,  # never captured under the old schema
        )
        fp = transaction_fingerprint(legacy_txn)
        store.add_new(
            NewTxnResult(
                new_transactions=(legacy_txn,),
                fingerprints=(fp,),
                duplicates_in_batch=0,
            )
        )
        store.set_categories({fp: "Groceries"})
        store.mark_file_processed(file_fingerprint(self._CB_TEXT.encode()))

    def test_reupload_of_processed_file_backfills_null_balance(self, tmp_path):
        store = Store(":memory:")
        self._seed_legacy_row(store)

        fake = FakeAnalyserClient()
        report = run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, self._CB_TEXT.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )

        rows = store.conn.execute("SELECT balance, category FROM transactions").fetchall()
        balances = store.account_balances("2026-06")
        store.close()

        # File was recognised as already-processed (counted skipped)...
        assert report.files_skipped == 1
        # ...but was still parsed, so the balance was backfilled.
        assert report.balance_updates == 1
        assert len(rows) == 1, "no duplicate row"
        assert rows[0]["balance"] == "1000.00"
        assert rows[0]["category"] == "Groceries", "category untouched by balance reconciliation"

        # Zero analyser calls — Layer 2 filters this row out as already-seen.
        assert fake.call_count == 0
        assert report.new_txns == 0
        assert report.categorised == 0
        assert report.model_used == ""
        assert report.noop is False, "balance_updates > 0 must NOT be reported as a no-op"

        # account_balances() now derives a real figure instead of unavailable.
        assert balances["commbank"] == {"opening": "1072.40", "closing": "1000.00"}

    def test_second_identical_reupload_is_a_true_noop(self, tmp_path):
        """Once the balance has been backfilled, re-uploading the same bytes
        again is a true no-op (balance_updates == 0, zero analyser calls)."""
        store = Store(":memory:")
        self._seed_legacy_row(store)
        fake = FakeAnalyserClient()

        run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, self._CB_TEXT.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )

        second = run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, self._CB_TEXT.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert second.noop is True
        assert second.balance_updates == 0
        assert fake.call_count == 0


# ---------------------------------------------------------------------------
# TestOrphanedNullCategoryRecovery — a row stored by a prior run whose categorise
# step then failed (or was rate-limited) is left with category NULL and its file
# already fingerprinted. Because it is "seen", Layer 2 excludes it from
# new_transactions — the pipeline must still recover it from the store and
# categorise it, instead of orphaning it forever.
# ---------------------------------------------------------------------------

class TestOrphanedNullCategoryRecovery:
    _CB_TEXT = "20/06/2026,-72.40,SYNTH ORPHAN SHOP,1000.00\n"

    def _seed_orphan(self, store: Store) -> None:
        """Persist a row with category NULL and mark its file processed — exactly
        the state left behind when add_new() succeeds but the following
        categorise() call fails. set_categories is deliberately NOT called."""
        txn = Transaction(
            date=_date(2026, 6, 20),
            description="SYNTH ORPHAN SHOP",
            amount=Decimal("-72.40"),
            bank=Bank.COMMBANK,
            balance=Decimal("1000.00"),
        )
        fp = transaction_fingerprint(txn)
        store.add_new(
            NewTxnResult(
                new_transactions=(txn,),
                fingerprints=(fp,),
                duplicates_in_batch=0,
            )
        )
        store.mark_file_processed(file_fingerprint(self._CB_TEXT.encode()))

    def test_reupload_of_processed_file_recovers_orphan(self, tmp_path):
        store = Store(":memory:")
        self._seed_orphan(store)
        assert len(store.uncategorised()) == 1  # precondition: one NULL-category row

        fake = FakeAnalyserClient("Groceries")
        report = run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, self._CB_TEXT.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        rows = store.conn.execute("SELECT category FROM transactions").fetchall()
        remaining = store.uncategorised()
        store.close()

        # Pending uncategorised work → NOT a no-op, even though nothing is new.
        assert report.noop is False
        assert report.files_skipped == 1  # file recognised as already processed
        assert report.new_txns == 0       # nothing genuinely new
        # The orphan is now categorised, and none remain NULL.
        assert rows[0]["category"] == "Groceries"
        assert remaining == []
        assert fake.call_count == 1
        assert report.categorised == 1

    def test_empty_reupload_also_recovers_orphan(self, tmp_path):
        """A run with NO uploads still recovers a pending orphan (not a no-op)."""
        store = Store(":memory:")
        self._seed_orphan(store)

        fake = FakeAnalyserClient("Dining Out")
        report = run_pipeline(
            [],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        cat = store.conn.execute("SELECT category FROM transactions").fetchone()["category"]
        store.close()

        assert report.noop is False
        assert cat == "Dining Out"
        assert fake.call_count == 1

    def test_second_run_after_recovery_is_a_true_noop(self, tmp_path):
        """Once recovered, re-uploading the same bytes is a true no-op again."""
        store = Store(":memory:")
        self._seed_orphan(store)
        fake = FakeAnalyserClient("Groceries")

        run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, self._CB_TEXT.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        calls_after_first = fake.call_count

        second = run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, self._CB_TEXT.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert second.noop is True
        assert fake.call_count == calls_after_first  # zero further LLM calls


# ---------------------------------------------------------------------------
# TestSplitwiseTagging — deterministic self-tag categorisation, pre-sanitise
# ---------------------------------------------------------------------------

class TestSplitwiseTagging:
    """A 'Splitwise <word>' tag in the raw description is categorised deterministically
    and NEVER sent to the LLM (the reference may carry a friend's name)."""

    # CommBank: no header, DD/MM/YYYY, signed amount, description, balance.
    # Row 1 is a self-tagged Splitwise transfer with a trailing friend name; row 2 is
    # an ordinary merchant that must still go through the LLM.
    _MIXED = (
        "20/06/2026,-40.00,SPLITWISE UTILITIES ALICE,1000.00\n"
        "21/06/2026,-25.00,SYNTH CORNER STORE,975.00\n"
    )
    _TAGGED_ONLY = "20/06/2026,-40.00,SPLITWISE FOOD BOB,1000.00\n"

    def _run(self, csv_text, store, fake, tmp_path):
        return run_pipeline(
            [UploadedFile("commbank.csv", Bank.COMMBANK, csv_text.encode())],
            store=store,
            analyser_client=fake,
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )

    def test_tagged_row_categorised_deterministically(self, tmp_path):
        store = Store(":memory:")
        fake = FakeAnalyserClient("Groceries")
        report = self._run(self._MIXED, store, fake, tmp_path)
        rows = {
            r["description"]: r["category"]
            for r in store.conn.execute("SELECT description, category FROM transactions")
        }
        store.close()

        # Tagged row -> Utilities (NOT the fake's 'Groceries' default).
        tagged = next(d for d in rows if "SPLITWISE UTILITIES" in d)
        assert rows[tagged] == "Utilities"
        # Ordinary row still LLM-categorised, so the pipeline did not stop.
        assert rows["SYNTH CORNER STORE"] == "Groceries"
        # Both rows counted as categorised (1 deterministic + 1 LLM).
        assert report.categorised == 2

    def test_tagged_row_never_reaches_the_llm_payload(self, tmp_path):
        store = Store(":memory:")
        fake = FakeAnalyserClient("Groceries")
        self._run(self._MIXED, store, fake, tmp_path)
        store.close()

        blob = " ".join(fake.received_user_prompts)
        # Privacy invariant: neither the tag nor the friend name leaves the machine.
        assert "SPLITWISE" not in blob
        assert "ALICE" not in blob
        # Exactly the one non-tagged row was sent to the analyser.
        total_items = sum(len(json.loads(p)) for p in fake.received_user_prompts)
        assert total_items == 1

    def test_tagged_only_upload_makes_zero_llm_calls(self, tmp_path):
        store = Store(":memory:")
        fake = FakeAnalyserClient("Groceries")
        report = self._run(self._TAGGED_ONLY, store, fake, tmp_path)
        cat = store.conn.execute("SELECT category FROM transactions").fetchone()[0]
        store.close()

        assert fake.call_count == 0          # payload empty -> no off-machine call
        assert cat == "Dining Out"           # SPLITWISE FOOD -> Dining Out
        assert report.categorised == 1


# ---------------------------------------------------------------------------
# TestPushNotificationInvocation — v2 Pass 3 (inert scaffold)
#
# Asserts pipeline.py only calls send_processed_notification behind the flag
# posture: a real (non-noop) run reaches the call site exactly once; a true
# no-op run does not reach it at all. The notifier itself is a hard no-op by
# default (see backend/notifier/test_notifier.py) — here we only verify the
# CALL SITE behaviour and that pipeline never lets a notifier exception break
# the run.
# ---------------------------------------------------------------------------

import backend.pipeline as pipeline_module


class TestPushNotificationInvocation:
    def test_real_run_calls_notifier_exactly_once(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            pipeline_module, "send_processed_notification", lambda store: calls.append(store) or 0
        )

        store = Store(":memory:")
        run_pipeline(
            _make_uploads(),
            store=store,
            analyser_client=FakeAnalyserClient(),
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert len(calls) == 1

    def test_noop_run_does_not_call_notifier(self, tmp_path, monkeypatch):
        """A truly-identical re-upload (noop path) never reaches the notifier call site."""
        calls = []
        monkeypatch.setattr(
            pipeline_module, "send_processed_notification", lambda store: calls.append(store) or 0
        )

        store = Store(":memory:")
        uploads = _make_uploads()
        run_pipeline(
            uploads,
            store=store,
            analyser_client=FakeAnalyserClient(),
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        calls.clear()  # only care about the second (no-op) run

        second = run_pipeline(
            uploads,
            store=store,
            analyser_client=FakeAnalyserClient(),
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert second.noop is True
        assert len(calls) == 0

    def test_default_config_notifier_is_a_genuine_no_op_and_does_not_raise(self, tmp_path):
        """With the REAL notifier (no monkeypatch) and default env (unset), the
        call site must not raise and RunReport must still be returned normally."""
        store = Store(":memory:")
        report = run_pipeline(
            _make_uploads(),
            store=store,
            analyser_client=FakeAnalyserClient(),
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert report is not None
        assert report.noop is False

    def test_notifier_exception_is_swallowed_run_report_still_returned(self, tmp_path, monkeypatch):
        """A raised exception inside the notifier must never fail the pipeline run."""

        def _boom(store):
            raise RuntimeError("synthetic notifier failure")

        monkeypatch.setattr(pipeline_module, "send_processed_notification", _boom)

        store = Store(":memory:")
        report = run_pipeline(
            _make_uploads(),
            store=store,
            analyser_client=FakeAnalyserClient(),
            drive_service=None,
            output_dir=tmp_path,
            sanitise_log_dir=tmp_path,
        )
        store.close()

        assert report is not None
        assert report.noop is False
        assert report.new_txns == 5
