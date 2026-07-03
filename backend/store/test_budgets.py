"""test_budgets.py — pytest suite for the v6 per-category budget Store additions.

Covers get_budgets/set_budget (namespaced app_settings keys), delete_setting, the
atomic claim_budget_alert once-per-(category, month, threshold) semantics, the reset
addition that wipes fired-state but preserves budgets, and schema idempotency for the
budget_alert_fired table.

ALL fixtures use SYNTHETIC data generated inline. No real transactions, no real
account numbers, no real CSVs. Every database is :memory: or tmp_path — NEVER the
real SQLITE_PATH / ./data/. No network calls anywhere in this file.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.store import BUDGET_CATEGORIES, Store


# ---------------------------------------------------------------------------
# delete_setting
# ---------------------------------------------------------------------------


class TestDeleteSetting:
    def test_deletes_existing_key(self):
        with Store(":memory:") as store:
            store.set_setting("k", "v")
            store.delete_setting("k")
            assert store.get_setting("k") is None

    def test_absent_key_is_noop(self):
        with Store(":memory:") as store:
            # No row exists — must not raise.
            store.delete_setting("never-set")
            assert store.get_setting("never-set") is None


# ---------------------------------------------------------------------------
# set_budget / get_budgets
# ---------------------------------------------------------------------------


class TestBudgetsRoundTrip:
    def test_empty_when_none_set(self):
        with Store(":memory:") as store:
            assert store.get_budgets() == {}

    def test_set_then_get_roundtrip(self):
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            budgets = store.get_budgets()
            assert budgets == {"Groceries": Decimal("300.00")}

    def test_stored_canonical_two_dp_text(self):
        with Store(":memory:") as store:
            store.set_budget("Dining Out", Decimal("250"))
            # The raw app_settings value is the canonical 2dp string.
            assert store.get_setting("budget:Dining Out") == "250.00"

    def test_upsert_replaces_not_duplicates(self):
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            store.set_budget("Groceries", Decimal("450"))
            assert store.get_budgets() == {"Groceries": Decimal("450.00")}
            (count,) = store.conn.execute(
                "SELECT COUNT(*) FROM app_settings WHERE key = 'budget:Groceries'"
            ).fetchone()
            assert count == 1

    def test_none_clears_the_budget(self):
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            store.set_budget("Groceries", None)
            assert store.get_budgets() == {}
            # The underlying row is deleted, not left blank.
            assert store.get_setting("budget:Groceries") is None

    def test_multiple_categories(self):
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            store.set_budget("Transport", Decimal("120.50"))
            assert store.get_budgets() == {
                "Groceries": Decimal("300.00"),
                "Transport": Decimal("120.50"),
            }

    def test_every_budgetable_category_accepted(self):
        with Store(":memory:") as store:
            for cat in BUDGET_CATEGORIES:
                store.set_budget(cat, Decimal("100"))
            assert set(store.get_budgets()) == set(BUDGET_CATEGORIES)


class TestSetBudgetValidation:
    def test_income_rejected(self):
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.set_budget("Income", Decimal("100"))

    def test_unknown_category_rejected(self):
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.set_budget("Crypto", Decimal("100"))

    def test_transfer_rejected(self):
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.set_budget("Transfer", Decimal("100"))

    def test_zero_amount_rejected(self):
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.set_budget("Groceries", Decimal("0"))

    def test_negative_amount_rejected(self):
        with Store(":memory:") as store:
            with pytest.raises(ValueError):
                store.set_budget("Groceries", Decimal("-5"))


class TestGetBudgetsFailClosedSkips:
    """get_budgets must never raise on hand-corrupted data — it skips junk rows."""

    def test_skips_non_budgetable_key(self):
        with Store(":memory:") as store:
            # Hand-insert a junk namespaced key for a category that is not budgetable.
            store.set_setting("budget:Bogus", "100.00")
            store.set_setting("budget:Income", "500.00")
            store.set_budget("Groceries", Decimal("300"))
            assert store.get_budgets() == {"Groceries": Decimal("300.00")}

    def test_skips_unparseable_value(self):
        with Store(":memory:") as store:
            # A corrupted stored value must be skipped, not crash the read.
            store.set_setting("budget:Groceries", "not-a-number")
            store.set_budget("Transport", Decimal("120"))
            assert store.get_budgets() == {"Transport": Decimal("120.00")}


# ---------------------------------------------------------------------------
# claim_budget_alert — atomic once-per-(category, month, threshold)
# ---------------------------------------------------------------------------


class TestClaimBudgetAlert:
    def test_first_claim_true_second_false(self):
        with Store(":memory:") as store:
            assert store.claim_budget_alert("Groceries", "2026-06", 80) is True
            assert store.claim_budget_alert("Groceries", "2026-06", 80) is False

    def test_different_threshold_claims_independently(self):
        with Store(":memory:") as store:
            assert store.claim_budget_alert("Groceries", "2026-06", 80) is True
            assert store.claim_budget_alert("Groceries", "2026-06", 100) is True

    def test_different_month_claims_independently(self):
        with Store(":memory:") as store:
            assert store.claim_budget_alert("Groceries", "2026-06", 80) is True
            assert store.claim_budget_alert("Groceries", "2026-07", 80) is True

    def test_different_category_claims_independently(self):
        with Store(":memory:") as store:
            assert store.claim_budget_alert("Groceries", "2026-06", 80) is True
            assert store.claim_budget_alert("Transport", "2026-06", 80) is True

    def test_claim_persists_a_single_row(self):
        with Store(":memory:") as store:
            store.claim_budget_alert("Groceries", "2026-06", 80)
            store.claim_budget_alert("Groceries", "2026-06", 80)  # duplicate ignored
            (count,) = store.conn.execute(
                "SELECT COUNT(*) FROM budget_alert_fired "
                "WHERE category='Groceries' AND year_month='2026-06' AND threshold=80"
            ).fetchone()
            assert count == 1


# ---------------------------------------------------------------------------
# reset_all_data — wipes fired-state, PRESERVES budgets
# ---------------------------------------------------------------------------


class TestResetPreservesBudgets:
    def test_wipes_fired_state_and_reports_count(self):
        with Store(":memory:") as store:
            store.claim_budget_alert("Groceries", "2026-06", 80)
            store.claim_budget_alert("Groceries", "2026-06", 100)
            counts = store.reset_all_data()
            assert counts["budget_alert_fired"] == 2
            (remaining,) = store.conn.execute(
                "SELECT COUNT(*) FROM budget_alert_fired"
            ).fetchone()
            assert remaining == 0

    def test_preserves_budget_settings(self):
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            store.set_budget("Transport", Decimal("120"))
            store.reset_all_data()
            # Budgets live in app_settings and survive the wipe.
            assert store.get_budgets() == {
                "Groceries": Decimal("300.00"),
                "Transport": Decimal("120.00"),
            }

    def test_empty_reset_reports_zero_fired(self):
        with Store(":memory:") as store:
            counts = store.reset_all_data()
            assert counts["budget_alert_fired"] == 0


# ---------------------------------------------------------------------------
# Schema idempotency — budget_alert_fired picked up on a pre-existing DB
# ---------------------------------------------------------------------------


class TestSchemaIdempotency:
    def test_reopen_and_reinit_keeps_claims(self, tmp_path):
        db = str(tmp_path / "budgets.sqlite")
        store = Store(db)
        try:
            store.init_schema()  # explicit re-run is a no-op (CREATE IF NOT EXISTS)
            assert store.claim_budget_alert("Groceries", "2026-06", 80) is True
        finally:
            store.close()

        # Reopen the same file: init_schema runs again on construction.
        store2 = Store(db)
        try:
            store2.init_schema()
            # The prior claim persisted and the table is intact.
            assert store2.claim_budget_alert("Groceries", "2026-06", 80) is False
            assert store2.claim_budget_alert("Groceries", "2026-06", 100) is True
        finally:
            store2.close()

    def test_table_exists_after_construction(self):
        with Store(":memory:") as store:
            row = store.conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='budget_alert_fired'"
            ).fetchone()
            assert row is not None
