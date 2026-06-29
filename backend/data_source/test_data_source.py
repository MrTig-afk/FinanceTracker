"""test_data_source.py — pytest suite for the §7.3 per-bank CSV parsers.

ALL fixtures use synthetic data generated inline.
No real transactions, no real account numbers, no real descriptions.
Never reads data/inbox/* or any tracked CSV file.
"""
from __future__ import annotations

import dataclasses
import datetime
from decimal import Decimal

import pytest

from backend.data_source import (
    Bank,
    Transaction,
    get_parser,
    parse_file,
    parse_text,
)
from backend.data_source.common import iter_csv_rows, parse_amount, parse_date

# ---------------------------------------------------------------------------
# Synthetic constants — invented values only; never real data
# ---------------------------------------------------------------------------

FAKE_ACCOUNT = "748007654321"   # fictional account number, not a real BSB or account


# ---------------------------------------------------------------------------
# CSV-building helpers
# ---------------------------------------------------------------------------

def _commbank_csv(*rows: str) -> str:
    """Return a CommBank CSV string (no header) from the given line strings."""
    return "\n".join(rows) + "\n"


def _westpac_csv(*data_rows: str) -> str:
    """Return a Westpac CSV string with the canonical header plus the given data rows."""
    header = "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial"
    return "\n".join([header] + list(data_rows)) + "\n"


# ---------------------------------------------------------------------------
# CommBank parser tests
# ---------------------------------------------------------------------------

class TestCommBankParser:
    """Tests for the CommBank CSV parser profile (FR-8)."""

    def test_debit_row_yields_negative_decimal(self):
        """A CommBank debit row produces a negative Decimal amount."""
        csv_text = _commbank_csv("15-01-2025,-42.50,ACME CAFE,200.00")
        txns = parse_text(csv_text, "commbank")
        assert len(txns) == 1
        assert txns[0].amount == Decimal("-42.50")
        assert txns[0].amount < 0

    def test_credit_row_yields_positive_decimal(self):
        """A CommBank credit row produces a positive Decimal amount."""
        csv_text = _commbank_csv("16-01-2025,150.00,SALARY PAYMENT,350.00")
        txns = parse_text(csv_text, "commbank")
        assert len(txns) == 1
        assert txns[0].amount == Decimal("150.00")
        assert txns[0].amount > 0

    def test_amount_type_is_decimal_not_float(self):
        """The amount field must be exactly Decimal, never float."""
        csv_text = _commbank_csv("20-03-2025,7.50,TEST MART,100.00")
        txns = parse_text(csv_text, "commbank")
        assert len(txns) == 1
        assert type(txns[0].amount) is Decimal
        assert not isinstance(txns[0].amount, float)

    def test_first_row_is_not_treated_as_header(self):
        """CommBank has no header; the very first data row must appear in results."""
        csv_text = _commbank_csv(
            "01-01-2025,-10.00,FIRST ROW SHOP,500.00",
            "02-01-2025,-20.00,SECOND ROW SHOP,480.00",
            "03-01-2025,-30.00,THIRD ROW SHOP,450.00",
        )
        txns = parse_text(csv_text, "commbank")
        # All 3 rows must be present — first row must NOT be discarded as header.
        assert len(txns) == 3
        assert txns[0].description == "FIRST ROW SHOP"

    def test_slash_date_separator_equals_dash(self):
        """DD/MM/YYYY and DD-MM-YYYY separators produce the same date."""
        csv_dash = _commbank_csv("10-06-2025,-5.00,ACME CAFE,100.00")
        csv_slash = _commbank_csv("10/06/2025,-5.00,ACME CAFE,100.00")
        txns_dash = parse_text(csv_dash, "commbank")
        txns_slash = parse_text(csv_slash, "commbank")
        assert txns_dash[0].date == txns_slash[0].date
        assert txns_dash[0].date == datetime.date(2025, 6, 10)

    def test_dollar_thousands_amount(self):
        """'$1,234.56' (quoted field) parses to Decimal('1234.56') exactly."""
        csv_text = _commbank_csv('20-01-2025,"$1,234.56",TEST MART,5000.00')
        txns = parse_text(csv_text, "commbank")
        assert len(txns) == 1
        assert txns[0].amount == Decimal("1234.56")

    def test_negative_dollar_symbol_amount(self):
        """-$40.00 in the amount field parses to Decimal('-40.00')."""
        csv_text = _commbank_csv("21-01-2025,-$40.00,ACME CAFE,200.00")
        txns = parse_text(csv_text, "commbank")
        assert len(txns) == 1
        assert txns[0].amount == Decimal("-40.00")

    def test_blank_and_whitespace_lines_skipped(self):
        """Blank and whitespace-only CSV lines produce no transactions."""
        csv_text = (
            "01-02-2025,-5.00,ACME CAFE,100.00\n"
            "\n"
            "   \n"
            "02-02-2025,-10.00,TEST MART,90.00\n"
        )
        txns = parse_text(csv_text, "commbank")
        assert len(txns) == 2

    def test_malformed_date_row_skipped_no_exception(self):
        """A row with an unparseable date is silently skipped; good rows are returned."""
        csv_text = _commbank_csv(
            "01-03-2025,-5.00,ACME CAFE,100.00",
            "NOT-A-DATE,-10.00,TEST MART,90.00",   # bad date — must be skipped
            "03-03-2025,-15.00,CORNER STORE,75.00",
        )
        # Must NOT raise; must return the 2 good rows only.
        txns = parse_text(csv_text, "commbank")
        assert len(txns) == 2
        descriptions = {t.description for t in txns}
        assert "ACME CAFE" in descriptions
        assert "CORNER STORE" in descriptions
        assert "TEST MART" not in descriptions

    def test_too_few_columns_skipped(self):
        """A row with fewer than 3 columns is silently skipped."""
        csv_text = (
            "01-04-2025,-5.00\n"                       # only 2 cols
            "02-04-2025,-10.00,TEST MART,80.00\n"
        )
        txns = parse_text(csv_text, "commbank")
        assert len(txns) == 1
        assert txns[0].description == "TEST MART"

    def test_quoted_description_with_comma(self):
        """A quoted description containing a comma is parsed as one field."""
        csv_text = _commbank_csv('01-05-2025,-8.50,"ACME CAFE, SYDNEY",200.00')
        txns = parse_text(csv_text, "commbank")
        assert len(txns) == 1
        assert txns[0].description == "ACME CAFE, SYDNEY"

    def test_date_field_is_datetime_date(self):
        """Transaction.date is a datetime.date, not a string."""
        csv_text = _commbank_csv("10-07-2025,-3.00,TEST MART,100.00")
        txns = parse_text(csv_text, "commbank")
        assert isinstance(txns[0].date, datetime.date)
        assert not isinstance(txns[0].date, str)

    def test_bank_field_is_commbank(self):
        """Every returned Transaction carries bank == Bank.COMMBANK."""
        csv_text = _commbank_csv(
            "10-09-2025,-5.00,ACME CAFE,100.00",
            "11-09-2025,50.00,SALARY,150.00",
        )
        txns = parse_text(csv_text, "commbank")
        assert all(t.bank == Bank.COMMBANK for t in txns)

    def test_transaction_is_frozen(self):
        """Transaction must be a frozen dataclass — mutation must raise."""
        csv_text = _commbank_csv("10-10-2025,-5.00,TEST MART,100.00")
        txns = parse_text(csv_text, "commbank")
        t = txns[0]
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
            t.amount = Decimal("0")  # type: ignore[misc]

    def test_parse_text_commbank_returns_list_of_transaction(self):
        """parse_text returns list[Transaction] for commbank."""
        csv_text = _commbank_csv("10-08-2025,-5.00,ACME CAFE,100.00")
        result = parse_text(csv_text, "commbank")
        assert isinstance(result, list)
        assert all(isinstance(t, Transaction) for t in result)


# ---------------------------------------------------------------------------
# Westpac parser tests
# ---------------------------------------------------------------------------

class TestWestpacParser:
    """Tests for the Westpac CSV parser profile (FR-9)."""

    def test_debit_only_row_yields_negative_amount(self):
        """A Westpac row with only a debit column populated yields a negative amount."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},15-01-2025,ACME CAFE,42.50,,200.00,PAYMENT,",
        )
        txns = parse_text(csv_text, "westpac")
        assert len(txns) == 1
        assert txns[0].amount == Decimal("-42.50")
        assert txns[0].amount < 0

    def test_credit_only_row_yields_positive_amount(self):
        """A Westpac row with only a credit column populated yields a positive amount."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},16-01-2025,SALARY DEPOSIT,,1500.00,1700.00,DEP,",
        )
        txns = parse_text(csv_text, "westpac")
        assert len(txns) == 1
        assert txns[0].amount == Decimal("1500.00")
        assert txns[0].amount > 0

    def test_both_empty_debit_credit_row_skipped(self):
        """A Westpac row where both debit and credit are empty is skipped (not a transaction)."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},17-01-2025,MYSTERY ENTRY,,,100.00,,",   # both empty
            f"{FAKE_ACCOUNT},18-01-2025,TEST MART,10.00,,90.00,PAYMENT,",
        )
        txns = parse_text(csv_text, "westpac")
        assert len(txns) == 1
        assert txns[0].description == "TEST MART"

    def test_account_number_absent_from_all_transaction_fields(self):
        """Privacy regression: column 0 (account number) must not appear in any Transaction field."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},20-01-2025,ACME CAFE,5.00,,200.00,PAYMENT,",
        )
        txns = parse_text(csv_text, "westpac")
        assert len(txns) == 1
        t = txns[0]
        # The fake account number must not leak into any string representation.
        assert FAKE_ACCOUNT not in t.description
        assert FAKE_ACCOUNT not in str(t.date)
        assert FAKE_ACCOUNT not in str(t.amount)
        assert FAKE_ACCOUNT not in str(t.bank)
        # The dataclass must have exactly the four expected fields — no bonus attribute.
        field_names = {f.name for f in dataclasses.fields(t)}
        assert field_names == {"date", "description", "amount", "bank"}

    def test_account_number_scientific_notation_absent_from_description(self):
        """Account number in scientific notation (e.g. 7.48007E+11) must not appear in description."""
        scientific_acct = "7.48007E+11"
        csv_text = (
            "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial\n"
            f"{scientific_acct},21-01-2025,TEST MART,15.00,,300.00,PAYMENT,\n"
        )
        txns = parse_text(csv_text, "westpac")
        assert len(txns) == 1
        assert scientific_acct not in txns[0].description

    def test_both_debit_credit_populated_net_signed_amount(self):
        """When both debit and credit are populated the amount is credit - debit."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},22-01-2025,REVERSAL ENTRY,10.00,30.00,200.00,MISC,",
        )
        txns = parse_text(csv_text, "westpac")
        assert len(txns) == 1
        # credit(30) - debit(10) = +20
        assert txns[0].amount == Decimal("20.00")

    def test_blank_and_whitespace_lines_skipped(self):
        """Blank and whitespace-only lines inside the Westpac CSV are ignored."""
        csv_text = (
            "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial\n"
            "\n"
            f"{FAKE_ACCOUNT},23-01-2025,ACME CAFE,5.00,,100.00,PAYMENT,\n"
            "   \n"
            f"{FAKE_ACCOUNT},24-01-2025,TEST MART,8.00,,92.00,PAYMENT,\n"
        )
        txns = parse_text(csv_text, "westpac")
        assert len(txns) == 2

    def test_malformed_date_row_skipped_no_exception(self):
        """A Westpac row with a bad date is silently skipped; good rows are returned."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},NOT-A-DATE,ACME CAFE,5.00,,100.00,PAYMENT,",   # bad date
            f"{FAKE_ACCOUNT},25-01-2025,TEST MART,8.00,,92.00,PAYMENT,",
        )
        txns = parse_text(csv_text, "westpac")
        assert len(txns) == 1
        assert txns[0].description == "TEST MART"

    def test_slash_date_separator_equals_dash(self):
        """DD/MM/YYYY and DD-MM-YYYY dates parse to the same datetime.date."""
        csv_dash = _westpac_csv(
            f"{FAKE_ACCOUNT},10-06-2025,ACME CAFE,5.00,,200.00,PAYMENT,",
        )
        csv_slash = _westpac_csv(
            f"{FAKE_ACCOUNT},10/06/2025,ACME CAFE,5.00,,200.00,PAYMENT,",
        )
        txns_dash = parse_text(csv_dash, "westpac")
        txns_slash = parse_text(csv_slash, "westpac")
        assert txns_dash[0].date == txns_slash[0].date
        assert txns_dash[0].date == datetime.date(2025, 6, 10)

    def test_trailing_empty_categories_serial_cells(self):
        """Westpac rows with blank Categories/Serial trailing cells are accepted."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},26-01-2025,TEST MART,12.00,,88.00,,",
        )
        txns = parse_text(csv_text, "westpac")
        assert len(txns) == 1
        assert txns[0].description == "TEST MART"

    def test_amount_type_is_decimal(self):
        """Westpac amounts are Decimal, not float."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},27-01-2025,ACME CAFE,99.99,,200.00,PAYMENT,",
        )
        txns = parse_text(csv_text, "westpac")
        assert type(txns[0].amount) is Decimal

    def test_bank_field_is_westpac(self):
        """Every returned Transaction carries bank == Bank.WESTPAC."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},28-01-2025,ACME CAFE,5.00,,100.00,PAYMENT,",
            f"{FAKE_ACCOUNT},29-01-2025,SALARY,,1000.00,1100.00,DEP,",
        )
        txns = parse_text(csv_text, "westpac")
        assert all(t.bank == Bank.WESTPAC for t in txns)

    def test_date_field_is_datetime_date(self):
        """Transaction.date is a datetime.date instance for Westpac rows."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},30-01-2025,TEST MART,5.00,,100.00,PAYMENT,",
        )
        txns = parse_text(csv_text, "westpac")
        assert isinstance(txns[0].date, datetime.date)
        assert not isinstance(txns[0].date, str)

    def test_header_only_returns_empty_list(self):
        """A Westpac CSV that contains only the header row returns an empty list."""
        csv_text = "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial\n"
        result = parse_text(csv_text, "westpac")
        assert result == []

    def test_parse_text_westpac_returns_list_of_transaction(self):
        """parse_text returns list[Transaction] for westpac."""
        csv_text = _westpac_csv(
            f"{FAKE_ACCOUNT},10-02-2025,ACME CAFE,5.00,,100.00,PAYMENT,",
        )
        result = parse_text(csv_text, "westpac")
        assert isinstance(result, list)
        assert all(isinstance(t, Transaction) for t in result)


# ---------------------------------------------------------------------------
# parse_amount helper tests
# ---------------------------------------------------------------------------

class TestParseAmount:
    """Unit tests for the common.parse_amount helper."""

    def test_dollar_thousands_separator(self):
        """'$1,234.56' -> Decimal('1234.56')."""
        assert parse_amount("$1,234.56") == Decimal("1234.56")

    def test_negative_dollar_symbol(self):
        """'-$40.00' -> Decimal('-40.00')."""
        assert parse_amount("-$40.00") == Decimal("-40.00")

    def test_plain_negative(self):
        """-42.50 -> Decimal('-42.50')."""
        assert parse_amount("-42.50") == Decimal("-42.50")

    def test_plain_positive(self):
        """7.5 -> Decimal('7.5')."""
        assert parse_amount("7.5") == Decimal("7.5")

    def test_empty_string_raises_value_error(self):
        """Empty string raises ValueError — fail-fast, never silently zero."""
        with pytest.raises(ValueError):
            parse_amount("")

    def test_whitespace_only_raises_value_error(self):
        """Whitespace-only string raises ValueError."""
        with pytest.raises(ValueError):
            parse_amount("   ")

    def test_result_is_decimal_not_float(self):
        """Result is always exactly Decimal, never float."""
        result = parse_amount("1234.56")
        assert type(result) is Decimal
        assert not isinstance(result, float)

    def test_quoted_dollar_thousands(self):
        """Amount wrapped in double-quotes with dollar and thousands sep parses correctly."""
        assert parse_amount('"$1,234.56"') == Decimal("1234.56")

    def test_leading_whitespace_stripped(self):
        """Leading and trailing whitespace is stripped before parsing."""
        assert parse_amount("  42.00  ") == Decimal("42.00")


# ---------------------------------------------------------------------------
# parse_date helper tests
# ---------------------------------------------------------------------------

class TestParseDate:
    """Unit tests for the common.parse_date helper."""

    def test_dash_separator_4digit_year(self):
        """DD-MM-YYYY parses to the correct date."""
        assert parse_date("15-06-2025") == datetime.date(2025, 6, 15)

    def test_slash_separator_4digit_year(self):
        """DD/MM/YYYY parses to the correct date."""
        assert parse_date("15/06/2025") == datetime.date(2025, 6, 15)

    def test_dash_and_slash_produce_same_date(self):
        """Both separators for the same date produce identical datetime.date values."""
        assert parse_date("10-03-2024") == parse_date("10/03/2024")

    def test_2digit_year_dash(self):
        """DD-MM-YY (2-digit year) is accepted and returns a datetime.date."""
        d = parse_date("10-06-25")
        assert isinstance(d, datetime.date)

    def test_2digit_year_slash(self):
        """DD/MM/YY (2-digit year) is accepted and returns a datetime.date."""
        d = parse_date("10/06/25")
        assert isinstance(d, datetime.date)

    def test_invalid_string_raises_value_error(self):
        """A genuinely bad date string raises ValueError."""
        with pytest.raises(ValueError):
            parse_date("NOT-A-DATE")

    def test_returns_date_not_datetime(self):
        """parse_date returns datetime.date, not datetime.datetime."""
        result = parse_date("01-01-2025")
        assert type(result) is datetime.date

    def test_leading_whitespace_stripped(self):
        """Leading/trailing whitespace in the raw string is tolerated."""
        assert parse_date("  15-06-2025  ") == datetime.date(2025, 6, 15)


# ---------------------------------------------------------------------------
# iter_csv_rows helper tests
# ---------------------------------------------------------------------------

class TestIterCsvRows:
    """Unit tests for the common.iter_csv_rows helper."""

    def test_header_discarded_when_has_header_true(self):
        """The first row is consumed as header when has_header=True."""
        text = "Col1,Col2\nval1,val2\nval3,val4\n"
        rows = list(iter_csv_rows(text, has_header=True))
        assert len(rows) == 2
        assert rows[0] == ["val1", "val2"]

    def test_all_rows_yielded_when_no_header(self):
        """All rows are yielded when has_header=False."""
        text = "val1,val2\nval3,val4\n"
        rows = list(iter_csv_rows(text, has_header=False))
        assert len(rows) == 2

    def test_blank_rows_are_skipped(self):
        """Blank and whitespace-only rows are not yielded."""
        text = "val1,val2\n\n   \nval3,val4\n"
        rows = list(iter_csv_rows(text, has_header=False))
        assert len(rows) == 2

    def test_quoted_field_containing_comma(self):
        """Quoted fields containing commas are treated as a single cell (RFC-4180)."""
        text = '"ACME CAFE, SYDNEY",42.50\n'
        rows = list(iter_csv_rows(text, has_header=False))
        assert len(rows) == 1
        assert rows[0][0] == "ACME CAFE, SYDNEY"

    def test_empty_input_yields_nothing(self):
        """Empty text produces no rows."""
        rows = list(iter_csv_rows("", has_header=False))
        assert rows == []

    def test_header_only_yields_nothing(self):
        """When has_header=True and there is only the header, nothing is yielded."""
        text = "Col1,Col2\n"
        rows = list(iter_csv_rows(text, has_header=True))
        assert rows == []


# ---------------------------------------------------------------------------
# get_parser / dispatch tests
# ---------------------------------------------------------------------------

class TestGetParser:
    """Tests for the get_parser factory and dispatch."""

    def test_unknown_string_raises_value_error(self):
        """get_parser('UNKNOWN') raises ValueError — the primary failure case."""
        with pytest.raises(ValueError):
            get_parser("UNKNOWN")

    def test_empty_string_raises_value_error(self):
        """get_parser('') raises ValueError."""
        with pytest.raises(ValueError):
            get_parser("")

    def test_commbank_string_lowercase(self):
        """get_parser('commbank') returns an instantiated parser."""
        parser = get_parser("commbank")
        assert parser is not None

    def test_commbank_enum(self):
        """get_parser(Bank.COMMBANK) returns an instantiated parser."""
        parser = get_parser(Bank.COMMBANK)
        assert parser is not None

    def test_westpac_string_lowercase(self):
        """get_parser('westpac') returns an instantiated parser."""
        parser = get_parser("westpac")
        assert parser is not None

    def test_westpac_enum(self):
        """get_parser(Bank.WESTPAC) returns an instantiated parser."""
        parser = get_parser(Bank.WESTPAC)
        assert parser is not None

    def test_string_case_insensitive(self):
        """Bank name strings are accepted case-insensitively."""
        assert get_parser("CommBank") is not None
        assert get_parser("WESTPAC") is not None
        assert get_parser("Westpac") is not None


# ---------------------------------------------------------------------------
# parse_text contract tests
# ---------------------------------------------------------------------------

class TestParseTextContract:
    """Tests verifying the parse_text interface contract."""

    def test_returns_list(self):
        """parse_text always returns a list."""
        result = parse_text(_commbank_csv("10-01-2025,-5.00,TEST MART,100.00"), "commbank")
        assert isinstance(result, list)

    def test_items_are_transaction_instances(self):
        """Every item in the returned list is a Transaction."""
        csv_text = _commbank_csv(
            "10-01-2025,-5.00,TEST MART,100.00",
            "11-01-2025,50.00,SALARY,150.00",
        )
        result = parse_text(csv_text, "commbank")
        assert all(isinstance(t, Transaction) for t in result)

    def test_date_fields_are_datetime_date(self):
        """All Transaction.date values are datetime.date (not strings)."""
        csv_text = _commbank_csv(
            "10-01-2025,-5.00,TEST MART,100.00",
            "11-01-2025,50.00,SALARY,150.00",
        )
        result = parse_text(csv_text, "commbank")
        for t in result:
            assert isinstance(t.date, datetime.date)

    def test_empty_csv_returns_empty_list(self):
        """Parsing an empty string returns [] without raising."""
        result = parse_text("", "commbank")
        assert result == []

    def test_unknown_bank_raises_value_error(self):
        """parse_text with an unknown bank name raises ValueError."""
        with pytest.raises(ValueError):
            parse_text("irrelevant", "UNKNOWN")


# ---------------------------------------------------------------------------
# parse_file tests — writes to pytest tmp_path only, never to data/ or tracked dirs
# ---------------------------------------------------------------------------

class TestParseFile:
    """Tests for the parse_file disk-I/O path."""

    def test_commbank_file_round_trip(self, tmp_path):
        """parse_file reads a synthetic CommBank CSV from a temp file correctly."""
        csv_content = (
            "10-01-2025,-5.00,TEST MART,100.00\n"
            "11-01-2025,50.00,SALARY,150.00\n"
        )
        tmp_csv = tmp_path / "synthetic_commbank.csv"
        tmp_csv.write_text(csv_content, encoding="utf-8")
        txns = parse_file(tmp_csv, "commbank")
        assert len(txns) == 2
        assert txns[0].description == "TEST MART"
        assert txns[1].description == "SALARY"

    def test_westpac_file_round_trip(self, tmp_path):
        """parse_file reads a synthetic Westpac CSV from a temp file correctly."""
        csv_content = (
            "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial\n"
            f"{FAKE_ACCOUNT},10-02-2025,ACME CAFE,12.00,,300.00,PAYMENT,\n"
            f"{FAKE_ACCOUNT},11-02-2025,SALARY,,2000.00,2300.00,DEP,\n"
        )
        tmp_csv = tmp_path / "synthetic_westpac.csv"
        tmp_csv.write_text(csv_content, encoding="utf-8")
        txns = parse_file(tmp_csv, "westpac")
        assert len(txns) == 2
        assert txns[0].description == "ACME CAFE"
        assert txns[0].amount == Decimal("-12.00")
        assert txns[1].description == "SALARY"
        assert txns[1].amount == Decimal("2000.00")
