"""test_splitwise_rule.py — unit tests for the Splitwise self-tag matcher.

ALL fixtures are synthetic strings generated inline. No real transaction data.
No IO, no network.
"""
from __future__ import annotations

import pytest

from backend.store.splitwise_rule import match_splitwise_tag


class TestRecognisedMappings:
    @pytest.mark.parametrize(
        "desc, expected",
        [
            ("Splitwise utilities", "Utilities"),
            ("Splitwise food", "Dining Out"),
            ("Splitwise dining", "Dining Out"),
        ],
    )
    def test_each_mapping(self, desc, expected):
        assert match_splitwise_tag(desc) == expected

    @pytest.mark.parametrize(
        "word",
        ["settle", "settled", "settlement", "payback"],
    )
    def test_settle_synonyms_map_to_other(self, word):
        assert match_splitwise_tag(f"Splitwise {word}") == "Other"


class TestTolerance:
    @pytest.mark.parametrize(
        "desc, expected",
        [
            ("splitwise utilities", "Utilities"),   # all lower
            ("SPLITWISE FOOD", "Dining Out"),        # all upper
            ("Splitwise Food", "Dining Out"),        # mixed
            ("SPLITWISE-UTILITIES", "Utilities"),    # hyphen
            ("SPLITWISE:FOOD", "Dining Out"),        # colon
            ("SPLITWISE_SETTLE", "Other"),           # underscore
            ("SPLITWISE   TRANSPORT", "Other"),      # multi-space, unmapped word -> Other
        ],
    )
    def test_case_and_separator_tolerance(self, desc, expected):
        assert match_splitwise_tag(desc) == expected

    @pytest.mark.parametrize(
        "desc, expected",
        [
            ("OSKO PAYMENT SPLITWISE UTILITIES ALICE", "Utilities"),
            ("PAYID SPLITWISE FOOD FROM BOB 0412345678", "Dining Out"),
            ("EFTPOS 12345 SPLITWISE SETTLE JOHN SMITH", "Other"),
        ],
    )
    def test_tag_found_amid_platform_noise(self, desc, expected):
        """The tag is recognised even when the bank wraps it with prefixes and a
        trailing friend name/number — the whole point of matching on the raw string."""
        assert match_splitwise_tag(desc) == expected


class TestFallback:
    @pytest.mark.parametrize(
        "desc",
        ["Splitwise misc", "SPLITWISE", "Splitwise groceries", "splitwise rent"],
    )
    def test_unknown_or_bare_tag_maps_to_other(self, desc):
        """Any 'Splitwise ...' with no recognised word (or bare) falls to Other."""
        assert match_splitwise_tag(desc) == "Other"


class TestNegatives:
    @pytest.mark.parametrize(
        "desc",
        [
            "WOOLWORTHS METRO",
            "SPLIT BILL CAFE",          # 'SPLIT' but not the whole word 'SPLITWISE'
            "MYSPLITWISEACCOUNT",       # no word boundary before SPLITWISE
            "",
        ],
    )
    def test_no_tag_returns_none(self, desc):
        assert match_splitwise_tag(desc) is None

    def test_none_input_returns_none(self):
        assert match_splitwise_tag(None) is None
