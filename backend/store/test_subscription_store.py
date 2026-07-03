"""test_subscription_store.py — pytest suite for the v6 subscription Store additions.

Covers upsert_subscription (insert / value-change update / no-op-when-unchanged),
claim_subscription_event (atomic once-per-(merchant, month, event) semantics),
get_subscriptions ordering, has_any_subscriptions, subscription_detection_rows
(Transfer legs excluded), the reset_all_data additions that wipe both new tables,
and schema idempotency for the two new tables on a pre-existing DB.

ALL fixtures use SYNTHETIC data generated inline. No real transactions, no real
account numbers, no real CSVs. Every database is :memory: or tmp_path — NEVER the
real SQLITE_PATH / ./data/. No network calls anywhere in this file.
"""
from __future__ import annotations

from decimal import Decimal

from backend.store import Store


def _seed_txn(store, fp, date, desc, amount, bank, category, ym):
    store.conn.execute(
        "INSERT INTO transactions"
        "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
        " VALUES (?,?,?,?,?,?,?,'t')",
        (fp, date, desc, amount, bank, category, ym),
    )
    store.conn.commit()


def _upsert(store, **overrides):
    kwargs = {
        "merchant_key": "spend:STREAMCO",
        "root": "STREAMCO",
        "direction": "spend",
        "expected_amount": Decimal("22.99"),
        "first_seen_month": "2026-04",
        "last_seen_month": "2026-06",
        "status": "active",
    }
    kwargs.update(overrides)
    return store.upsert_subscription(**kwargs)


# ---------------------------------------------------------------------------
# upsert_subscription — insert / value-change update / no-op-when-unchanged
# ---------------------------------------------------------------------------


class TestUpsertSubscription:
    def test_insert_returns_true(self):
        with Store(":memory:") as store:
            assert _upsert(store) is True
            assert len(store.get_subscriptions()) == 1

    def test_value_change_update_returns_true(self):
        with Store(":memory:") as store:
            _upsert(store)
            # Flip status -> a real change -> the guarded upsert writes.
            assert _upsert(store, status="ended") is True
            assert store.get_subscriptions()[0]["status"] == "ended"

    def test_expected_amount_change_updates(self):
        with Store(":memory:") as store:
            _upsert(store)
            assert _upsert(store, expected_amount=Decimal("25.99")) is True
            assert store.get_subscriptions()[0]["expected_amount"] == "25.99"

    def test_noop_when_unchanged_returns_false(self):
        with Store(":memory:") as store:
            _upsert(store)
            before = store.get_subscriptions()[0]["updated_at"]
            # Identical values -> guarded upsert writes NOTHING.
            assert _upsert(store) is False
            after = store.get_subscriptions()[0]["updated_at"]
            assert after == before

    def test_upsert_never_duplicates_the_merchant_key(self):
        with Store(":memory:") as store:
            _upsert(store)
            _upsert(store, status="ended")
            (count,) = store.conn.execute(
                "SELECT COUNT(*) FROM subscriptions WHERE merchant_key='spend:STREAMCO'"
            ).fetchone()
            assert count == 1

    def test_expected_amount_stored_canonical_two_dp(self):
        with Store(":memory:") as store:
            _upsert(store, expected_amount=Decimal("40"))
            assert store.get_subscriptions()[0]["expected_amount"] == "40.00"


# ---------------------------------------------------------------------------
# claim_subscription_event — atomic once-per-(merchant, month, event)
# ---------------------------------------------------------------------------


class TestClaimSubscriptionEvent:
    def test_first_claim_true_second_false(self):
        with Store(":memory:") as store:
            assert store.claim_subscription_event("spend:STREAMCO", "2026-06", "new") is True
            assert store.claim_subscription_event("spend:STREAMCO", "2026-06", "new") is False

    def test_different_event_claims_independently(self):
        with Store(":memory:") as store:
            assert store.claim_subscription_event("spend:STREAMCO", "2026-06", "new") is True
            assert store.claim_subscription_event(
                "spend:STREAMCO", "2026-06", "price_change"
            ) is True

    def test_different_month_claims_independently(self):
        with Store(":memory:") as store:
            assert store.claim_subscription_event("spend:STREAMCO", "2026-06", "new") is True
            assert store.claim_subscription_event("spend:STREAMCO", "2026-07", "new") is True

    def test_different_merchant_claims_independently(self):
        with Store(":memory:") as store:
            assert store.claim_subscription_event("spend:STREAMCO", "2026-06", "new") is True
            assert store.claim_subscription_event("spend:GYMCO", "2026-06", "new") is True

    def test_claim_persists_a_single_row(self):
        with Store(":memory:") as store:
            store.claim_subscription_event("spend:STREAMCO", "2026-06", "new")
            store.claim_subscription_event("spend:STREAMCO", "2026-06", "new")  # ignored
            (count,) = store.conn.execute(
                "SELECT COUNT(*) FROM subscription_event_fired "
                "WHERE merchant_key='spend:STREAMCO' AND year_month='2026-06' AND event='new'"
            ).fetchone()
            assert count == 1


# ---------------------------------------------------------------------------
# get_subscriptions ordering + has_any_subscriptions
# ---------------------------------------------------------------------------


class TestGetSubscriptions:
    def test_empty_when_none(self):
        with Store(":memory:") as store:
            assert store.get_subscriptions() == []

    def test_active_before_ended_then_root_alpha(self):
        with Store(":memory:") as store:
            _upsert(store, merchant_key="spend:ZEBRA", root="ZEBRA", status="active")
            _upsert(store, merchant_key="spend:ALPHA", root="ALPHA", status="ended")
            _upsert(store, merchant_key="spend:BRAVO", root="BRAVO", status="active")
            roots = [s["root"] for s in store.get_subscriptions()]
            # active (alpha order) first, then ended.
            assert roots == ["BRAVO", "ZEBRA", "ALPHA"]

    def test_has_any_subscriptions(self):
        with Store(":memory:") as store:
            assert store.has_any_subscriptions() is False
            _upsert(store)
            assert store.has_any_subscriptions() is True


# ---------------------------------------------------------------------------
# subscription_detection_rows — Transfer legs excluded, ordered
# ---------------------------------------------------------------------------


class TestSubscriptionDetectionRows:
    def test_excludes_transfer_rows(self):
        with Store(":memory:") as store:
            _seed_txn(store, "a", "2026-06-01", "STREAMCO", "-22.99",
                      "commbank", "Subscriptions", "2026-06")
            _seed_txn(store, "b", "2026-06-02", "SYNTH XFER", "-500.00",
                      "commbank", "Transfer", "2026-06")
            rows = store.subscription_detection_rows()
            descs = [r["description"] for r in rows]
            assert descs == ["STREAMCO"]  # Transfer leg excluded

    def test_includes_null_category_rows(self):
        with Store(":memory:") as store:
            _seed_txn(store, "a", "2026-06-01", "STREAMCO", "-22.99",
                      "commbank", None, "2026-06")
            rows = store.subscription_detection_rows()
            assert len(rows) == 1
            assert rows[0]["category"] is None

    def test_ordered_by_year_month(self):
        with Store(":memory:") as store:
            _seed_txn(store, "b", "2026-06-01", "B", "-1.00", "commbank", None, "2026-06")
            _seed_txn(store, "a", "2026-04-01", "A", "-1.00", "commbank", None, "2026-04")
            rows = store.subscription_detection_rows()
            assert [r["year_month"] for r in rows] == ["2026-04", "2026-06"]


# ---------------------------------------------------------------------------
# reset_all_data — wipes both new tables and reports counts
# ---------------------------------------------------------------------------


class TestResetWipesSubscriptionTables:
    def test_wipes_both_tables_and_reports_counts(self):
        with Store(":memory:") as store:
            _upsert(store, merchant_key="spend:STREAMCO", root="STREAMCO")
            _upsert(store, merchant_key="spend:GYMCO", root="GYMCO")
            store.claim_subscription_event("spend:STREAMCO", "2026-06", "new")

            counts = store.reset_all_data()

            assert counts["subscriptions"] == 2
            assert counts["subscription_event_fired"] == 1
            (subs,) = store.conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()
            (fired,) = store.conn.execute(
                "SELECT COUNT(*) FROM subscription_event_fired"
            ).fetchone()
            assert subs == 0
            assert fired == 0

    def test_empty_reset_reports_zero(self):
        with Store(":memory:") as store:
            counts = store.reset_all_data()
            assert counts["subscriptions"] == 0
            assert counts["subscription_event_fired"] == 0


# ---------------------------------------------------------------------------
# Schema idempotency — both tables appear on a pre-existing DB
# ---------------------------------------------------------------------------


class TestSchemaIdempotency:
    def test_tables_exist_after_construction(self):
        with Store(":memory:") as store:
            for name in ("subscriptions", "subscription_event_fired"):
                row = store.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (name,),
                ).fetchone()
                assert row is not None, f"{name} table missing"

    def test_reopen_and_reinit_keeps_rows(self, tmp_path):
        db = str(tmp_path / "subs.sqlite")
        store = Store(db)
        try:
            store.init_schema()  # explicit re-run is a no-op (CREATE IF NOT EXISTS)
            _upsert(store)
            assert store.claim_subscription_event("spend:STREAMCO", "2026-06", "new") is True
        finally:
            store.close()

        # Reopen the same file: init_schema runs again on construction.
        store2 = Store(db)
        try:
            store2.init_schema()
            assert store2.has_any_subscriptions() is True
            # The prior claim persisted and the table is intact.
            assert store2.claim_subscription_event("spend:STREAMCO", "2026-06", "new") is False
        finally:
            store2.close()
