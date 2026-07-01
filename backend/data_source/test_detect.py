"""test_detect.py — content-based bank detection (detect.py).

SYNTHETIC CSV text only, generated in code. No real transactions.
"""
from __future__ import annotations

import pytest

from backend.data_source import Bank, detect_bank

# CommBank: no header, date in column 0.
_CB_TEXT = (
    "20/06/2026,-72.40,WOOLWORTHS METRO,1000.00\n"
    "21/06/2026,-18.90,SYNTH TRANSPORT CO,927.60\n"
)

# Westpac: header row; account number in column 0, date in column 1.
_WP_TEXT = (
    "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance,Categories,Serial\n"
    "748007654321,23/06/2026,SYNTH UTILITY BILL,130.05,,2000.00,,\n"
    "748007654321,24/06/2026,SYNTH SALARY CREDIT,,3200.00,5200.00,,\n"
)


class TestDetectBank:
    def test_commbank_detected(self):
        assert detect_bank(_CB_TEXT) is Bank.COMMBANK

    def test_westpac_detected(self):
        assert detect_bank(_WP_TEXT) is Bank.WESTPAC

    def test_commbank_with_dash_dates(self):
        assert detect_bank("20-06-2026,-5.00,SYNTH SHOP,10.00\n") is Bank.COMMBANK

    def test_empty_text_is_none(self):
        assert detect_bank("") is None

    def test_whitespace_only_is_none(self):
        assert detect_bank("\n  \n\n") is None

    @pytest.mark.parametrize(
        "text",
        [
            "not,valid,csv\nstill,not,valid\n",  # no dates anywhere
            "bad\ncontent\nhere\n",               # single-column junk
            "hello world\n",                      # free text
        ],
    )
    def test_garbage_is_none(self, text):
        assert detect_bank(text) is None

    def test_westpac_header_only_is_none(self):
        # Header but no data rows -> cannot confirm Westpac shape.
        header = "Bank Account,Date,Narrative,Debit Amount,Credit Amount,Balance\n"
        assert detect_bank(header) is None

    def test_leading_blank_line_tolerated(self):
        assert detect_bank("\n20/06/2026,-5.00,SYNTH SHOP,10.00\n") is Bank.COMMBANK
