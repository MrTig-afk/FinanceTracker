"""test_xlsx.py — pytest suite for .xlsx statement ingestion (xlsx.py).

ALL fixtures are SYNTHETIC .xlsx workbooks BUILT in-memory with openpyxl.
No real transactions, no real account numbers, no real descriptions, and no
real/tracked .xlsx files are ever read from disk.

The xlsx path is a thin normaliser: it flattens the first worksheet to CSV text
and hands it to the SAME detect_bank + per-bank parsers as a CSV. So these tests
assert the round trip end-to-end: bytes -> Transaction list, for both banks.
"""
from __future__ import annotations

import datetime
import io
from decimal import Decimal

import openpyxl
import pytest

from backend.data_source import (
    Bank,
    detect_bank,
    parse_text,
    upload_to_csv_text,
    looks_like_xlsx,
    xlsx_to_csv_text,
)

# Fictional account number — not a real BSB/account. Dropped by the Westpac profile.
FAKE_ACCOUNT = "748007654321"


# ---------------------------------------------------------------------------
# Synthetic .xlsx builders (in-memory bytes only)
# ---------------------------------------------------------------------------

def _xlsx_bytes(rows: list[list]) -> bytes:
    """Return .xlsx bytes for a single worksheet built from the given cell rows.

    Cells may be str / int / float / datetime / None — mirroring what a bank's
    real Excel export would carry (dates as real date cells, amounts as numbers).
    """
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    for row in rows:
        worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _commbank_xlsx() -> bytes:
    """CommBank shape: NO header; Date, Amount(signed), Description, Balance.

    Uses real date cells and numeric amounts to exercise cell-type coercion.
    """
    return _xlsx_bytes(
        [
            [datetime.date(2025, 1, 15), -42.50, "SYNTH CAFE", 200.00],
            [datetime.date(2025, 1, 16), 3200.00, "SYNTH SALARY CREDIT", 3400.00],
            [datetime.date(2025, 1, 17), -18.90, "SYNTH TRANSPORT CO", 3381.10],
        ]
    )


def _westpac_xlsx() -> bytes:
    """Westpac shape: header row; leading account-number column; split debit/credit."""
    return _xlsx_bytes(
        [
            ["Bank Account", "Date", "Narrative", "Debit Amount",
             "Credit Amount", "Balance", "Categories", "Serial"],
            [FAKE_ACCOUNT, datetime.date(2026, 6, 23), "SYNTH UTILITY BILL",
             130.05, None, 2000.00, None, None],
            [FAKE_ACCOUNT, datetime.date(2026, 6, 24), "SYNTH SALARY CREDIT",
             None, 3200.00, 5200.00, None, None],
        ]
    )


# ---------------------------------------------------------------------------
# xlsx detection
# ---------------------------------------------------------------------------

class TestLooksLikeXlsx:
    def test_zip_magic_bytes_detected(self):
        content = _commbank_xlsx()
        assert content[:4] == b"PK\x03\x04"          # sanity: it is a real zip
        assert looks_like_xlsx(content, "commbank.csv") is True  # magic beats ext

    def test_filename_extension_detected(self):
        # No magic bytes, but a .xlsx name still routes to the xlsx reader.
        assert looks_like_xlsx(b"not a zip", "statement.xlsx") is True
        assert looks_like_xlsx(b"not a zip", "STATEMENT.XLSX") is True

    def test_plain_csv_is_not_xlsx(self):
        assert looks_like_xlsx(b"15/01/2025,-5.00,SYNTH SHOP,10.00\n", "x.csv") is False

    def test_no_filename_falls_back_to_magic(self):
        assert looks_like_xlsx(_westpac_xlsx(), None) is True
        assert looks_like_xlsx(b"plain,text", None) is False


# ---------------------------------------------------------------------------
# CommBank .xlsx round trip
# ---------------------------------------------------------------------------

class TestCommBankXlsx:
    def test_detects_as_commbank(self):
        text = xlsx_to_csv_text(_commbank_xlsx())
        assert detect_bank(text) is Bank.COMMBANK

    def test_parses_signed_amounts(self):
        text = upload_to_csv_text(_commbank_xlsx(), "commbank.xlsx")
        txns = parse_text(text, detect_bank(text))
        assert len(txns) == 3
        # Debit stays negative, credit stays positive (single signed column).
        assert txns[0].amount == Decimal("-42.50")
        assert txns[1].amount == Decimal("3200.00")
        assert txns[2].amount == Decimal("-18.90")

    def test_dates_and_descriptions_and_balance(self):
        text = upload_to_csv_text(_commbank_xlsx(), "commbank.xlsx")
        txns = parse_text(text, detect_bank(text))
        assert txns[0].date == datetime.date(2025, 1, 15)
        assert txns[0].description == "SYNTH CAFE"
        assert txns[0].balance == Decimal("200.00")
        assert txns[0].bank is Bank.COMMBANK
        # Amounts are Decimal, never float (no binary-float artefacts).
        assert isinstance(txns[0].amount, Decimal)


# ---------------------------------------------------------------------------
# Westpac .xlsx round trip
# ---------------------------------------------------------------------------

class TestWestpacXlsx:
    def test_detects_as_westpac(self):
        text = xlsx_to_csv_text(_westpac_xlsx())
        assert detect_bank(text) is Bank.WESTPAC

    def test_account_number_dropped_and_split_columns_merged(self):
        text = upload_to_csv_text(_westpac_xlsx(), "westpac.xlsx")
        txns = parse_text(text, detect_bank(text))
        assert len(txns) == 2

        # Debit populated -> negative; credit populated -> positive.
        assert txns[0].amount == Decimal("-130.05")
        assert txns[1].amount == Decimal("3200.00")

        # The dropped account number must appear NOWHERE in the parsed output
        # (not in any description; there is no field that could carry it).
        for txn in txns:
            assert FAKE_ACCOUNT not in txn.description

    def test_dates_descriptions_balance(self):
        text = upload_to_csv_text(_westpac_xlsx(), "westpac.xlsx")
        txns = parse_text(text, detect_bank(text))
        assert txns[0].date == datetime.date(2026, 6, 23)
        assert txns[0].description == "SYNTH UTILITY BILL"
        assert txns[0].balance == Decimal("2000.00")
        assert txns[1].balance == Decimal("5200.00")
        assert txns[0].bank is Bank.WESTPAC


# ---------------------------------------------------------------------------
# Failure / safety cases
# ---------------------------------------------------------------------------

class TestXlsxFailureHandling:
    def test_malformed_xlsx_bytes_raise_value_error(self):
        # ZIP magic bytes but a truncated / non-workbook body: must fail SAFELY
        # with ValueError (which the pipeline treats as an unrecognised file),
        # never an unhandled crash and never a leaked openpyxl detail.
        bogus = b"PK\x03\x04" + b"this is not a real xlsx workbook body"
        with pytest.raises(ValueError):
            xlsx_to_csv_text(bogus)

    def test_upload_router_propagates_value_error_on_bad_xlsx(self):
        bogus = b"PK\x03\x04garbage"
        with pytest.raises(ValueError):
            upload_to_csv_text(bogus, "statement.xlsx")

    def test_csv_bytes_pass_through_unchanged(self):
        # Non-xlsx bytes must decode as CSV text, identical to the old path.
        csv_bytes = "15/01/2025,-5.00,SYNTH SHOP,10.00\n".encode("utf-8")
        assert upload_to_csv_text(csv_bytes, "commbank.csv") == \
            "15/01/2025,-5.00,SYNTH SHOP,10.00\n"

    def test_utf8_bom_csv_is_stripped(self):
        csv_bytes = "﻿15/01/2025,-5.00,SYNTH SHOP,10.00\n".encode("utf-8")
        text = upload_to_csv_text(csv_bytes, "commbank.csv")
        assert not text.startswith("﻿")
        assert detect_bank(text) is Bank.COMMBANK
