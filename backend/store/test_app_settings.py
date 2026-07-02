"""test_app_settings.py — pytest suite for the Feature E store additions (§7.7).

Covers the app_settings key/value store, notification opt-out flags, the learned-
corrections list/delete helpers, the CSV export row builder, and the destructive
reset path.

ALL fixtures use synthetic data generated inline. No real transactions, no real
descriptions, no real account numbers. Every database is :memory: — NEVER the real
SQLITE_PATH / ./data/. No network calls anywhere in this file.
"""
from __future__ import annotations

from backend.store import DEFAULT_CONTEXT, Store


# ---------------------------------------------------------------------------
# app_settings — get/set + bool coercion
# ---------------------------------------------------------------------------


class TestAppSettings:
    def test_get_unset_returns_none(self):
        with Store(":memory:") as store:
            assert store.get_setting("nothing") is None

    def test_set_then_get_roundtrip(self):
        with Store(":memory:") as store:
            store.set_setting("k", "v")
            assert store.get_setting("k") == "v"

    def test_set_upserts_not_duplicates(self):
        with Store(":memory:") as store:
            store.set_setting("k", "one")
            store.set_setting("k", "two")
            assert store.get_setting("k") == "two"
            (count,) = store.conn.execute(
                "SELECT COUNT(*) FROM app_settings WHERE key = 'k'"
            ).fetchone()
            assert count == 1

    def test_bool_default_when_unset(self):
        with Store(":memory:") as store:
            assert store.get_bool_setting("flag", True) is True
            assert store.get_bool_setting("flag", False) is False

    def test_set_bool_true_stored_as_one(self):
        with Store(":memory:") as store:
            store.set_bool_setting("flag", True)
            assert store.get_setting("flag") == "1"
            assert store.get_bool_setting("flag", False) is True

    def test_set_bool_false_stored_as_zero(self):
        with Store(":memory:") as store:
            store.set_bool_setting("flag", False)
            assert store.get_setting("flag") == "0"
            assert store.get_bool_setting("flag", True) is False

    def test_bool_tolerant_truthy_parsing(self):
        with Store(":memory:") as store:
            for raw in ("1", "true", "TRUE", "Yes", "on", " On "):
                store.set_setting("flag", raw)
                assert store.get_bool_setting("flag", False) is True
            for raw in ("0", "false", "no", "off", "", "banana"):
                store.set_setting("flag", raw)
                assert store.get_bool_setting("flag", True) is False


# ---------------------------------------------------------------------------
# notification_enabled — per-type opt-out, default True
# ---------------------------------------------------------------------------


class TestNotificationEnabled:
    def test_default_true_when_unset(self):
        with Store(":memory:") as store:
            assert store.notification_enabled("processed") is True

    def test_reads_notify_prefixed_key(self):
        with Store(":memory:") as store:
            store.set_bool_setting("notify:processed", False)
            assert store.notification_enabled("processed") is False
            # A different type is unaffected (still default True).
            assert store.notification_enabled("parse_error") is True

    def test_reenable_flips_back(self):
        with Store(":memory:") as store:
            store.set_bool_setting("notify:processed", False)
            store.set_bool_setting("notify:processed", True)
            assert store.notification_enabled("processed") is True


# ---------------------------------------------------------------------------
# list_corrections / delete_correction
# ---------------------------------------------------------------------------


class TestCorrectionsListDelete:
    def test_empty_list(self):
        with Store(":memory:") as store:
            assert store.list_corrections() == []

    def test_list_shape_and_newest_first(self):
        with Store(":memory:") as store:
            store.record_correction("SYNTH ALPHA", "Groceries")
            store.record_correction("SYNTH BETA", "Transport")
            rows = store.list_corrections()
        assert [r["cleaned_description"] for r in rows] == ["SYNTH BETA", "SYNTH ALPHA"]
        for r in rows:
            assert set(r.keys()) == {"id", "cleaned_description", "category", "created_at"}
            assert isinstance(r["id"], int)

    def test_delete_removes_one(self):
        with Store(":memory:") as store:
            store.record_correction("SYNTH ALPHA", "Groceries")
            store.record_correction("SYNTH BETA", "Transport")
            target = store.list_corrections()[0]["id"]
            removed = store.delete_correction(target)
            assert removed == 1
            remaining = [r["cleaned_description"] for r in store.list_corrections()]
            assert remaining == ["SYNTH ALPHA"]

    def test_delete_missing_returns_zero(self):
        with Store(":memory:") as store:
            assert store.delete_correction(999999) == 0


# ---------------------------------------------------------------------------
# all_transactions_for_export
# ---------------------------------------------------------------------------


def _seed_txn(store, fp, date, desc, amount, bank, category, ym):
    store.conn.execute(
        "INSERT INTO transactions"
        "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
        " VALUES (?,?,?,?,?,?,?,'t')",
        (fp, date, desc, amount, bank, category, ym),
    )
    store.conn.commit()


class TestExportRows:
    def test_empty_returns_empty_list(self):
        with Store(":memory:") as store:
            assert store.all_transactions_for_export() == []

    def test_rows_ordered_by_date_then_id(self):
        with Store(":memory:") as store:
            _seed_txn(store, "f2", "2026-06-02", "SYNTH B", "-2.00", "commbank", "Groceries", "2026-06")
            _seed_txn(store, "f1", "2026-06-01", "SYNTH A", "-1.00", "westpac", None, "2026-06")
            rows = store.all_transactions_for_export()
        assert [r["date"] for r in rows] == ["2026-06-01", "2026-06-02"]
        # NULL category is passed through as None (endpoint renders it as "").
        assert rows[0]["category"] is None
        assert set(rows[0].keys()) == {
            "date", "description", "amount", "category", "bank", "year_month",
        }
        assert rows[0]["amount"] == "-1.00"


# ---------------------------------------------------------------------------
# reset_all_data
# ---------------------------------------------------------------------------


class TestResetAllData:
    def test_wipes_data_and_returns_counts(self):
        with Store(":memory:") as store:
            _seed_txn(store, "f1", "2026-06-01", "SYNTH A", "-1.00", "commbank", "Groceries", "2026-06")
            _seed_txn(store, "f2", "2026-06-02", "SYNTH B", "-2.00", "commbank", "Transport", "2026-06")
            store.mark_file_processed("filefp-1")
            store.record_correction("SYNTH MERCHANT", "Groceries")

            counts = store.reset_all_data()
            assert counts == {"transactions": 2, "file_fingerprints": 1, "corrections": 1}

            assert store.all_transactions_for_export() == []
            assert store.list_corrections() == []
            assert store.is_file_processed("filefp-1") is False

    def test_reseeds_category_context_to_defaults(self):
        with Store(":memory:") as store:
            store.reset_all_data()
            ctx = store.get_category_context()
        assert len(ctx) == len(DEFAULT_CONTEXT) == 8

    def test_preserves_push_subscription_and_settings(self):
        with Store(":memory:") as store:
            store.upsert_push_subscription(
                {
                    "endpoint": "https://example.test/push/SYNTH",
                    "keys": {"p256dh": "synth_p", "auth": "synth_a"},
                }
            )
            store.set_bool_setting("corrections_enabled", True)

            store.reset_all_data()

            assert len(store.list_push_subscriptions()) == 1
            assert store.get_bool_setting("corrections_enabled", False) is True

    def test_empty_db_reset_is_safe_noop_counts(self):
        with Store(":memory:") as store:
            counts = store.reset_all_data()
            assert counts == {"transactions": 0, "file_fingerprints": 0, "corrections": 0}
            assert len(store.get_category_context()) == 8
