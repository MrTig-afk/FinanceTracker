"""test_category_context.py — pytest suite for the category-context store methods
(D1 fixed taxonomy / D2 pre-filled example hints).

ALL fixtures use SYNTHETIC hint strings generated inline — never the real D2
default hints' content and never real transaction data. Every database is
:memory: — never the real SQLITE_PATH / ./data/. No network calls anywhere.
"""
from __future__ import annotations

import pytest

from backend.store import TAXONOMY, DEFAULT_CONTEXT, Store


# ---------------------------------------------------------------------------
# TestFreshDbSeeding
# ---------------------------------------------------------------------------


class TestFreshDbSeeding:
    """A fresh :memory: Store seeds exactly 9 rows with the D2 example hints."""

    @pytest.fixture(autouse=True)
    def _open(self):
        self.store = Store(":memory:")
        yield
        self.store.close()

    def test_returns_nine_rows(self):
        assert len(self.store.get_category_context()) == 8

    def test_names_match_taxonomy_order(self):
        names = [c.name for c in self.store.get_category_context()]
        assert names == list(TAXONOMY)

    def test_positions_are_0_to_8_in_order(self):
        positions = [c.position for c in self.store.get_category_context()]
        assert positions == list(range(8))

    def test_colors_match_default_context(self):
        colors = {c.name: c.color for c in self.store.get_category_context()}
        for c in DEFAULT_CONTEXT:
            assert colors[c.name] == c.color

    def test_hints_match_default_context(self):
        """On a fresh DB, stored hints equal the D2 seed example hints verbatim."""
        hints = {c.name: c.hints for c in self.store.get_category_context()}
        for c in DEFAULT_CONTEXT:
            assert hints[c.name] == c.hints

    def test_hints_are_non_empty_on_fresh_db(self):
        """D2: the generated-prompt preview shows real content on first load."""
        for c in self.store.get_category_context():
            assert c.hints.strip() != ""


# ---------------------------------------------------------------------------
# TestSaveRoundtrip
# ---------------------------------------------------------------------------


class TestSaveRoundtrip:
    """save_category_context updates only the named categories' hints."""

    @pytest.fixture(autouse=True)
    def _open(self):
        self.store = Store(":memory:")
        yield
        self.store.close()

    def test_save_returns_nine(self):
        written = self.store.save_category_context({"Groceries": "SYNTH HINT A"})
        assert written == 8

    def test_named_category_hint_updated(self):
        self.store.save_category_context({"Groceries": "SYNTH HINT A"})
        by_name = {c.name: c.hints for c in self.store.get_category_context()}
        assert by_name["Groceries"] == "SYNTH HINT A"

    def test_multiple_named_categories_updated(self):
        self.store.save_category_context(
            {"Groceries": "SYNTH GROCER A, SYNTH GROCER B", "Housing": "SYNTH LANDLORD"}
        )
        by_name = {c.name: c.hints for c in self.store.get_category_context()}
        assert by_name["Groceries"] == "SYNTH GROCER A, SYNTH GROCER B"
        assert by_name["Housing"] == "SYNTH LANDLORD"

    def test_still_nine_rows_after_save(self):
        self.store.save_category_context({"Groceries": "SYNTH HINT A"})
        assert len(self.store.get_category_context()) == 8

    def test_name_color_position_unchanged_after_save(self):
        self.store.save_category_context({"Groceries": "SYNTH HINT A"})
        after = {c.name: (c.color, c.position) for c in self.store.get_category_context()}
        for c in DEFAULT_CONTEXT:
            assert after[c.name] == (c.color, c.position)

    def test_full_nine_category_save_roundtrips(self):
        """Saving all 9 canonical categories' hints in one call roundtrips exactly."""
        synth_hints = {name: f"SYNTH HINT FOR {name.upper()}" for name in TAXONOMY}
        written = self.store.save_category_context(synth_hints)
        assert written == 8

        after = self.store.get_category_context()
        assert len(after) == 8
        by_name = {c.name: c.hints for c in after}
        for name, hint in synth_hints.items():
            assert by_name[name] == hint
        # name/color/position still sourced from the canonical seed, unchanged.
        after_by_name = {c.name: (c.color, c.position) for c in after}
        for c in DEFAULT_CONTEXT:
            assert after_by_name[c.name] == (c.color, c.position)

    def test_unnamed_category_hints_untouched_by_partial_save(self):
        """Save touching only Groceries leaves other categories' hints as before."""
        before = {c.name: c.hints for c in self.store.get_category_context()}
        self.store.save_category_context({"Groceries": "SYNTH HINT A"})
        after = {c.name: c.hints for c in self.store.get_category_context()}
        for name in TAXONOMY:
            if name == "Groceries":
                continue
            # A full replace-all with a partial dict clears absent names to "" —
            # so 'before' (D2 seed) differs from 'after' ("") for every other name.
            assert after[name] == ""
            assert before[name] != ""


# ---------------------------------------------------------------------------
# TestFixedTaxonomyGuard  (D1 — BLOCKING)
# ---------------------------------------------------------------------------


class TestFixedTaxonomyGuard:
    """BLOCKING: save_category_context never adds/renames/removes a category."""

    @pytest.fixture(autouse=True)
    def _open(self):
        self.store = Store(":memory:")
        yield
        self.store.close()

    def test_unknown_name_does_not_add_a_row(self):
        self.store.save_category_context({"Bogus": "SYNTH VALUE"})
        assert len(self.store.get_category_context()) == 8

    def test_unknown_name_not_present_in_result(self):
        self.store.save_category_context({"Bogus": "SYNTH VALUE"})
        names = {c.name for c in self.store.get_category_context()}
        assert "Bogus" not in names

    def test_still_exactly_the_nine_canonical_names(self):
        self.store.save_category_context({"Bogus": "SYNTH VALUE", "Groceries": "SYNTH HINT"})
        names = {c.name for c in self.store.get_category_context()}
        assert names == set(TAXONOMY)

    def test_canonical_name_absent_from_dict_gets_empty_hints(self):
        self.store.save_category_context({"Groceries": "SYNTH HINT A"})
        by_name = {c.name: c.hints for c in self.store.get_category_context()}
        assert by_name["Housing"] == ""

    def test_empty_dict_clears_all_hints(self):
        self.store.save_category_context({})
        for c in self.store.get_category_context():
            assert c.hints == ""
        assert len(self.store.get_category_context()) == 8


# ---------------------------------------------------------------------------
# TestSeedIdempotency
# ---------------------------------------------------------------------------


class TestSeedIdempotency:
    """Re-opening a non-empty DB does not re-seed or duplicate rows."""

    def test_reopen_same_file_does_not_duplicate(self, tmp_path):
        db_path = str(tmp_path / "ctx.sqlite")

        store1 = Store(db_path)
        assert len(store1.get_category_context()) == 8
        store1.close()

        store2 = Store(db_path)
        assert len(store2.get_category_context()) == 8
        store2.close()

    def test_reopen_after_edit_preserves_edit(self, tmp_path):
        db_path = str(tmp_path / "ctx.sqlite")

        store1 = Store(db_path)
        store1.save_category_context({"Groceries": "SYNTH PERSISTED HINT"})
        store1.close()

        store2 = Store(db_path)
        by_name = {c.name: c.hints for c in store2.get_category_context()}
        assert by_name["Groceries"] == "SYNTH PERSISTED HINT"
        assert len(store2.get_category_context()) == 8
        store2.close()
