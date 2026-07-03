"""test_scorecard.py — pytest suite for the v7 categoriser scorecard (feature 4).

Covers Store.record_override_event(), Store.categoriser_scorecard(), and the
reset wipe of the `override_events` table.

ALL fixtures use SYNTHETIC data generated inline.
No real transactions, no real descriptions, no real account numbers, no real CSVs.
No network calls anywhere in this file.
Every database is :memory: — NEVER the real SQLITE_PATH / ./data/.

The event log stores ONLY (id, created_at, from_category, to_category): a
schema-level privacy assertion below proves nothing else can be persisted.
"""
from __future__ import annotations

import backend.store.store as store_module
from backend.store import Store


# ---------------------------------------------------------------------------
# Synthetic helpers — invented values; never real data
# ---------------------------------------------------------------------------


def _insert_txn(
    store: Store,
    *,
    fp: str,
    created_at: str,
    category: str | None,
    date: str = "2026-06-01",
    amount: str = "-10.00",
    bank: str = "commbank",
    ym: str = "2026-06",
) -> int:
    """Insert one synthetic transaction with a controlled created_at + category.

    Returns the new row id. created_at drives the scorecard's ingest-month
    denominator (substr(created_at, 1, 7)); category NULL means "not categorised".
    """
    cur = store.conn.execute(
        "INSERT INTO transactions"
        "(txn_fingerprint, date, description, amount, bank, category, year_month, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fp, date, "SYNTH MERCHANT", amount, bank, category, ym, created_at),
    )
    store.conn.commit()
    return int(cur.lastrowid)


def _insert_event(store: Store, *, created_at: str, from_category, to_category: str) -> None:
    """Insert one override_events row directly with a controlled created_at."""
    store.conn.execute(
        "INSERT INTO override_events(created_at, from_category, to_category) "
        "VALUES (?, ?, ?)",
        (created_at, from_category, to_category),
    )
    store.conn.commit()


def _events(store: Store) -> list:
    return store.conn.execute(
        "SELECT id, created_at, from_category, to_category FROM override_events ORDER BY id"
    ).fetchall()


def _event_count(store: Store) -> int:
    return store.conn.execute("SELECT COUNT(*) FROM override_events").fetchone()[0]


def _freeze_now(monkeypatch, iso: str) -> None:
    """Freeze module-level _utc_now_iso so both writes and the window end are fixed."""
    monkeypatch.setattr(store_module, "_utc_now_iso", lambda: iso)


def _month(scorecard: dict, ym: str) -> dict:
    for entry in scorecard["months"]:
        if entry["month"] == ym:
            return entry
    raise AssertionError(f"{ym} not in window {[m['month'] for m in scorecard['months']]}")


# ---------------------------------------------------------------------------
# 1. record_override_event writes exactly one row with only the four columns
# ---------------------------------------------------------------------------


class TestRecordOverrideEvent:
    def test_writes_one_row_with_from_and_to(self) -> None:
        with Store(":memory:") as store:
            store.record_override_event("Groceries", "Dining Out")

            rows = _events(store)
            assert len(rows) == 1
            assert rows[0]["from_category"] == "Groceries"
            assert rows[0]["to_category"] == "Dining Out"

    def test_created_at_uses_utc_now_iso(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-06-15T09:00:00+00:00")
            store.record_override_event("Groceries", "Transport")

            assert _events(store)[0]["created_at"] == "2026-06-15T09:00:00+00:00"

    def test_schema_privacy_only_four_columns(self) -> None:
        """PRIVACY: override_events may hold ONLY id/created_at/from_category/to_category.

        A regression that added a description/amount/row-id/fingerprint column would
        surface here — the table shape is the privacy contract.
        """
        with Store(":memory:") as store:
            cols = {row[1] for row in store.conn.execute("PRAGMA table_info(override_events)")}
            assert cols == {"id", "created_at", "from_category", "to_category"}

    def test_null_from_category_remediation_is_stored(self) -> None:
        """An Uncategorised remediation (from == None) IS logged (row exists)."""
        with Store(":memory:") as store:
            store.record_override_event(None, "Groceries")

            rows = _events(store)
            assert len(rows) == 1
            assert rows[0]["from_category"] is None
            assert rows[0]["to_category"] == "Groceries"

    def test_repeat_change_to_new_category_logs_each(self) -> None:
        with Store(":memory:") as store:
            store.record_override_event("Groceries", "Dining Out")
            store.record_override_event("Dining Out", "Transport")

            assert _event_count(store) == 2


# ---------------------------------------------------------------------------
# 2. No-op override (from == to) writes nothing (D-3)
# ---------------------------------------------------------------------------


class TestNoOpOverrideSkipped:
    def test_same_category_writes_nothing(self) -> None:
        with Store(":memory:") as store:
            store.record_override_event("Groceries", "Groceries")
            assert _event_count(store) == 0

    def test_both_none_writes_nothing(self) -> None:
        with Store(":memory:") as store:
            store.record_override_event(None, None)  # type: ignore[arg-type]
            assert _event_count(store) == 0

    def test_change_after_noop_still_logs(self) -> None:
        with Store(":memory:") as store:
            store.record_override_event("Groceries", "Groceries")  # skipped
            store.record_override_event("Groceries", "Transport")  # logged
            assert _event_count(store) == 1


# ---------------------------------------------------------------------------
# 3. NULL-from events are stored but EXCLUDED from `corrected` (D-2)
# ---------------------------------------------------------------------------


class TestNullFromExcludedFromCorrected:
    def test_remediation_not_counted_as_correction(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            # A real LLM error (from a category) + a remediation (from NULL) in 2026-06.
            _insert_event(store, created_at="2026-06-10T00:00:00+00:00",
                          from_category="Groceries", to_category="Dining Out")
            _insert_event(store, created_at="2026-06-11T00:00:00+00:00",
                          from_category=None, to_category="Transport")
            # 10 auto-categorised rows ingested in 2026-06 so accuracy is defined.
            for i in range(10):
                _insert_txn(store, fp=f"f{i}", created_at="2026-06-05T00:00:00+00:00",
                            category="Groceries")

            june = _month(store.categoriser_scorecard(months=3), "2026-06")
            # Both events exist in the table, but only the non-NULL-from one counts.
            assert _event_count(store) == 2
            assert june["corrected"] == 1
            assert june["auto_categorised"] == 10
            assert june["accuracy_pct"] == 90


# ---------------------------------------------------------------------------
# 4. Denominator counts by INGEST month; NULL-category rows do not count (D-5)
# ---------------------------------------------------------------------------


class TestDenominatorByIngestMonth:
    def test_only_non_null_category_rows_count(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            _insert_txn(store, fp="a", created_at="2026-06-01T00:00:00+00:00", category="Groceries")
            _insert_txn(store, fp="b", created_at="2026-06-02T00:00:00+00:00", category="Transport")
            _insert_txn(store, fp="c", created_at="2026-06-03T00:00:00+00:00", category=None)

            june = _month(store.categoriser_scorecard(months=3), "2026-06")
            assert june["auto_categorised"] == 2  # NULL-category row excluded

    def test_grouped_by_created_at_not_statement_month(self, monkeypatch) -> None:
        """A June statement row ingested in July counts in JULY, not June."""
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            # year_month (statement) = 2026-06 but created_at (ingest) = 2026-07.
            _insert_txn(store, fp="late", created_at="2026-07-02T00:00:00+00:00",
                        category="Groceries", ym="2026-06")

            card = store.categoriser_scorecard(months=3)
            assert _month(card, "2026-06")["auto_categorised"] == 0
            assert _month(card, "2026-07")["auto_categorised"] == 1


# ---------------------------------------------------------------------------
# 5. Retry backfill: a NULL row filled later joins its INGEST month (D-5)
# ---------------------------------------------------------------------------


class TestRetryBackfill:
    def test_null_then_filled_joins_ingest_month(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            # Ingested in June, left uncategorised by a failed run.
            rid = _insert_txn(store, fp="retry", created_at="2026-06-20T00:00:00+00:00",
                              category=None)

            before = store.categoriser_scorecard(months=3)
            assert _month(before, "2026-06")["auto_categorised"] == 0

            # A later retry (in July) fills the category — created_at is unchanged.
            store.set_categories({rid: "Groceries"})

            after = store.categoriser_scorecard(months=3)
            assert _month(after, "2026-06")["auto_categorised"] == 1  # joined June
            assert _month(after, "2026-07")["auto_categorised"] == 0  # not July

    def test_rereads_are_idempotent(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            _insert_txn(store, fp="x", created_at="2026-06-01T00:00:00+00:00", category="Groceries")

            first = store.categoriser_scorecard(months=3)
            second = store.categoriser_scorecard(months=3)
            assert first == second


# ---------------------------------------------------------------------------
# 6. Accuracy math: 96%, min(corrected, auto) clamp, None when auto == 0 (D-6)
# ---------------------------------------------------------------------------


class TestAccuracyMath:
    def _seed_month(self, store, *, auto: int, corrected: int, ym="2026-06") -> None:
        for i in range(auto):
            _insert_txn(store, fp=f"{ym}-a{i}", created_at=f"{ym}-05T00:00:00+00:00",
                        category="Groceries")
        for i in range(corrected):
            _insert_event(store, created_at=f"{ym}-06T00:00:00+00:00",
                          from_category="Groceries", to_category="Transport")

    def test_103_auto_4_corrected_is_96(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            self._seed_month(store, auto=103, corrected=4)

            june = _month(store.categoriser_scorecard(months=3), "2026-06")
            assert june["auto_categorised"] == 103
            assert june["corrected"] == 4
            assert june["accuracy_pct"] == 96  # 99/103 = 96.1... -> 96 HALF_UP

    def test_corrected_exceeds_auto_clamps_to_zero(self, monkeypatch) -> None:
        """Cross-month correction of an earlier-ingested row must not go negative."""
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            self._seed_month(store, auto=2, corrected=5)

            june = _month(store.categoriser_scorecard(months=3), "2026-06")
            assert june["accuracy_pct"] == 0  # not negative

    def test_auto_zero_yields_null_even_with_corrections(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            _insert_event(store, created_at="2026-06-06T00:00:00+00:00",
                          from_category="Groceries", to_category="Transport")

            june = _month(store.categoriser_scorecard(months=3), "2026-06")
            assert june["auto_categorised"] == 0
            assert june["corrected"] == 1
            assert june["accuracy_pct"] is None

    def test_perfect_month_is_100(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            self._seed_month(store, auto=5, corrected=0)

            assert _month(store.categoriser_scorecard(months=3), "2026-06")["accuracy_pct"] == 100

    def test_accuracy_pct_is_int_not_decimal(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            self._seed_month(store, auto=4, corrected=1)

            pct = _month(store.categoriser_scorecard(months=3), "2026-06")["accuracy_pct"]
            assert isinstance(pct, int)
            assert pct == 75


# ---------------------------------------------------------------------------
# 7. Window shape: ascending, zero-months present, current, clamp [1, 24] (D-7)
# ---------------------------------------------------------------------------


class TestWindowShape:
    def test_ascending_and_current_is_last(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            card = store.categoriser_scorecard(months=6)

            months = [m["month"] for m in card["months"]]
            assert months == ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]
            assert months == sorted(months)  # ascending
            assert card["current"] == card["months"][-1]
            assert card["current"]["month"] == "2026-07"

    def test_zero_data_months_present_with_nulls(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            card = store.categoriser_scorecard(months=3)

            empty = _month(card, "2026-05")
            assert empty == {"month": "2026-05", "auto_categorised": 0,
                             "corrected": 0, "accuracy_pct": None}

    def test_window_crosses_year_boundary(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-01-15T00:00:00+00:00")
            card = store.categoriser_scorecard(months=3)
            assert [m["month"] for m in card["months"]] == ["2025-11", "2025-12", "2026-01"]

    def test_upper_clamp_to_24(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            card = store.categoriser_scorecard(months=100)
            assert card["window"] == 24
            assert len(card["months"]) == 24

    def test_lower_clamp_to_1(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            card = store.categoriser_scorecard(months=0)
            assert card["window"] == 1
            assert len(card["months"]) == 1
            assert card["months"][0]["month"] == "2026-07"

    def test_empty_db_shape(self, monkeypatch) -> None:
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            card = store.categoriser_scorecard(months=6)
            assert card["window"] == 6
            assert len(card["months"]) == 6
            assert all(m["auto_categorised"] == 0 and m["corrected"] == 0
                       and m["accuracy_pct"] is None for m in card["months"])

    def test_events_outside_window_excluded(self, monkeypatch) -> None:
        """An event older than the window's first month must not leak in."""
        with Store(":memory:") as store:
            _freeze_now(monkeypatch, "2026-07-15T00:00:00+00:00")
            _insert_txn(store, fp="old", created_at="2026-01-01T00:00:00+00:00",
                        category="Groceries")
            _insert_event(store, created_at="2026-01-02T00:00:00+00:00",
                          from_category="Groceries", to_category="Transport")

            # months=3 window is 2026-05..2026-07; January is out of range.
            card = store.categoriser_scorecard(months=3)
            assert all(m["auto_categorised"] == 0 and m["corrected"] == 0
                       for m in card["months"])


# ---------------------------------------------------------------------------
# 8. reset_all_data wipes override_events and reports the pre-wipe count (D-8)
# ---------------------------------------------------------------------------


class TestResetWipesEvents:
    def test_reports_count_and_empties_table(self) -> None:
        with Store(":memory:") as store:
            store.record_override_event("Groceries", "Transport")
            store.record_override_event("Transport", "Dining Out")

            counts = store.reset_all_data()
            assert counts["override_events"] == 2
            assert _event_count(store) == 0

    def test_empty_db_reports_zero(self) -> None:
        with Store(":memory:") as store:
            counts = store.reset_all_data()
            assert counts["override_events"] == 0
