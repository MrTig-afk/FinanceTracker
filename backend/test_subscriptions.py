"""test_subscriptions.py — pytest suite for backend/subscriptions.py (v6 feature 4).

Covers the pure normalisation + streak-detection helpers (normalise_root,
build_groups, amounts_close, detect_segments, month arithmetic), the
check_subscriptions orchestration (bootstrap silence, aggregated new, price change,
missed income, idempotency, guard, per-type gate), the BLOCKING privacy assertion
(payloads carry NO merchant roots and NO dollar amounts), and the pipeline trigger.

ALL fixtures use SYNTHETIC data generated in code. No real transactions, no real CSVs,
no real merchants. Synthetic merchants only (STREAMCO, GYMCO, CLOUDCO, ACME SALARY).
Every Store is :memory: or tmp_path. NO live network: the notifier is either a
monkeypatched capture, a real in-memory Store with the default hard no-op, or driven
with a fake ``pywebpush`` injected via sys.modules plus obviously-synthetic VAPID keys.
"""
from __future__ import annotations

import json
import sys
import types
from decimal import Decimal

import pytest

import backend.subscriptions as subscriptions
from backend.subscriptions import (
    MIN_STREAK_MONTHS,
    Segment,
    amounts_close,
    build_groups,
    check_subscriptions,
    detect_segments,
    normalise_root,
    _months_between,
    _next_month,
)
from backend.store import Store


# ---------------------------------------------------------------------------
# Synthetic notifier plumbing (mirrors test_budget_alerts.py — no real keys/network)
# ---------------------------------------------------------------------------

_SYNTH_PUBLIC_KEY = "SYNTHETIC_TEST_VAPID_PUBLIC_KEY_NOT_REAL_abc123"
_SYNTH_PRIVATE_KEY = "SYNTHETIC_TEST_VAPID_PRIVATE_KEY_NOT_REAL_xyz789"
_SYNTH_SUBJECT = "mailto:synthetic-test@example.test"
_SYNTH_SUB = {
    "endpoint": "https://example.test/push/SYNTH_ENDPOINT",
    "keys": {"p256dh": "synth_p256dh", "auth": "synth_auth"},
}


def _enabled_config() -> dict:
    return {
        "enabled": True,
        "public_key": _SYNTH_PUBLIC_KEY,
        "private_key": _SYNTH_PRIVATE_KEY,
        "subject": _SYNTH_SUBJECT,
    }


@pytest.fixture(autouse=True)
def _clean_pywebpush_module():
    """A fake pywebpush injected by one test must never leak into another."""
    had = "pywebpush" in sys.modules
    original = sys.modules.get("pywebpush")
    yield
    if had:
        sys.modules["pywebpush"] = original
    else:
        sys.modules.pop("pywebpush", None)


def _install_capturing_pywebpush(monkeypatch):
    """Inject a fake pywebpush whose webpush() records the JSON payload string."""
    captured: list[str] = []
    fake = types.ModuleType("pywebpush")

    class WebPushException(Exception):
        pass

    def _fake_webpush(*, data, **kwargs):
        captured.append(data)

    fake.webpush = _fake_webpush
    fake.WebPushException = WebPushException
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    return captured


class _RecordingSend:
    """Callable stand-in for send_notification recording (ntype, count, detail)."""

    def __init__(self):
        self.calls: list[tuple[str, int | None, str | None]] = []

    def __call__(self, store, ntype, *, count=None, detail=None, config=None):
        self.calls.append((ntype, count, detail))
        return 1  # simulate one successful delivery


def _seed_txn(store, fp, date, desc, amount, bank, category, ym):
    """Insert one synthetic transaction row directly (bypasses idempotency layer)."""
    store.conn.execute(
        "INSERT INTO transactions"
        "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
        " VALUES (?,?,?,?,?,?,?,'t')",
        (fp, date, desc, amount, bank, category, ym),
    )
    store.conn.commit()


def _seed_months(store, prefix, desc, months, amount, *, bank="commbank", category=None):
    """Seed one row per (YYYY-MM) in `months` for a single merchant."""
    for i, ym in enumerate(months):
        _seed_txn(store, f"{prefix}{i}", f"{ym}-01", desc, amount, bank, category, ym)


# ---------------------------------------------------------------------------
# normalise_root — sanitiser scrub reuse + fail-closed residual gate + upper
# ---------------------------------------------------------------------------


class TestNormaliseRoot:
    def test_strips_digits_and_uppercases(self):
        # A store-number digit run is stripped; the merchant survives, uppercased.
        assert normalise_root("StreamCo 12345 Sydney") == "STREAMCO SYDNEY"

    def test_already_clean_is_uppercased(self):
        assert normalise_root("gymco fitness") == "GYMCO FITNESS"

    def test_p2p_name_only_fails_closed_to_none(self):
        # A PayID/P2P narrative scrubs to empty -> fail closed -> None.
        assert normalise_root("From Alex Smith to PayID Phone 0400 111 222") is None

    def test_none_row_excluded_from_grouping(self):
        rows = [
            _row("-22.99", "From Alex Smith to PayID Phone 0400 111 222", None, "2026-04"),
            _row("-22.99", "From Alex Smith to PayID Phone 0400 111 222", None, "2026-05"),
            _row("-22.99", "From Alex Smith to PayID Phone 0400 111 222", None, "2026-06"),
        ]
        # Every row's root is None -> no group is ever formed.
        assert build_groups(rows) == {}


# ---------------------------------------------------------------------------
# Month arithmetic helpers (pure)
# ---------------------------------------------------------------------------


class TestMonthMath:
    def test_next_month_within_year(self):
        assert _next_month("2026-04") == "2026-05"

    def test_next_month_year_rollover(self):
        assert _next_month("2026-12") == "2027-01"

    def test_months_between_signed(self):
        assert _months_between("2026-04", "2026-06") == 2
        assert _months_between("2026-06", "2026-04") == -2
        assert _months_between("2026-04", "2026-04") == 0

    def test_months_between_across_year(self):
        assert _months_between("2025-11", "2026-02") == 3


# ---------------------------------------------------------------------------
# amounts_close — max($1.00, 5%) tolerance boundaries
# ---------------------------------------------------------------------------


class TestAmountsClose:
    def test_dollar_branch_boundary_inside(self):
        # Small amount: max(1.00, 0.05*10) = 1.00. diff == 1.00 is inside (<=).
        assert amounts_close(Decimal("11.00"), Decimal("10.00")) is True

    def test_dollar_branch_boundary_outside(self):
        assert amounts_close(Decimal("11.01"), Decimal("10.00")) is False

    def test_percent_branch_boundary_inside(self):
        # 5% of 5000 = 250. diff == 250 is inside.
        assert amounts_close(Decimal("5250.00"), Decimal("5000.00")) is True

    def test_percent_branch_boundary_outside(self):
        assert amounts_close(Decimal("5251.00"), Decimal("5000.00")) is False

    def test_gradual_drift_stays_close(self):
        assert amounts_close(Decimal("15.49"), Decimal("14.99")) is True


# ---------------------------------------------------------------------------
# detect_segments — maximal consecutive in-tolerance streaks
# ---------------------------------------------------------------------------


class TestDetectSegments:
    def test_happy_three_month_streak(self):
        segs = detect_segments(
            {"2026-04": Decimal("22.99"), "2026-05": Decimal("22.99"),
             "2026-06": Decimal("22.99")}
        )
        assert segs == [Segment("2026-04", "2026-06", Decimal("22.99"))]
        assert segs[0].length == 3
        assert segs[0].length >= MIN_STREAK_MONTHS

    def test_gap_month_splits_segments(self):
        segs = detect_segments(
            {"2026-04": Decimal("10.00"), "2026-05": Decimal("10.00"),
             "2026-07": Decimal("10.00")}
        )
        assert [(s.start, s.end) for s in segs] == [
            ("2026-04", "2026-05"), ("2026-07", "2026-07")
        ]

    def test_out_of_tolerance_jump_splits(self):
        segs = detect_segments(
            {"2026-04": Decimal("22.99"), "2026-05": Decimal("22.99"),
             "2026-06": Decimal("40.00")}
        )
        assert [(s.start, s.end) for s in segs] == [
            ("2026-04", "2026-05"), ("2026-06", "2026-06")
        ]

    def test_in_tolerance_drift_stays_one_segment(self):
        segs = detect_segments(
            {"2026-04": Decimal("14.99"), "2026-05": Decimal("15.49"),
             "2026-06": Decimal("15.99")}
        )
        assert len(segs) == 1
        assert segs[0].last_amount == Decimal("15.99")  # tracks the latest month

    def test_single_month_never_detects(self):
        segs = detect_segments({"2026-06": Decimal("22.99")})
        assert max(s.length for s in segs) < MIN_STREAK_MONTHS

    def test_two_month_streak_never_detects(self):
        segs = detect_segments(
            {"2026-05": Decimal("22.99"), "2026-06": Decimal("22.99")}
        )
        assert max(s.length for s in segs) < MIN_STREAK_MONTHS


# ---------------------------------------------------------------------------
# build_groups — direction/root grouping + monthly-cadence qualification
# ---------------------------------------------------------------------------


def _row(amount, desc, category, ym):
    """A dict shaped like a subscription_detection_rows() sqlite3.Row."""
    return {"amount": amount, "description": desc, "category": category, "year_month": ym}


class TestBuildGroups:
    def test_spend_groups_by_direction_and_root(self):
        rows = [
            _row("-22.99", "STREAMCO", "Subscriptions", "2026-04"),
            _row("-22.99", "STREAMCO", "Subscriptions", "2026-05"),
        ]
        groups = build_groups(rows)
        assert ("spend", "STREAMCO") in groups
        assert groups[("spend", "STREAMCO")] == {
            "2026-04": Decimal("22.99"), "2026-05": Decimal("22.99")
        }

    def test_income_requires_income_category(self):
        rows = [_row("5000.00", "ACME SALARY", "Income", "2026-04")]
        assert ("income", "ACME SALARY") in build_groups(rows)

    def test_positive_not_income_is_ignored(self):
        # A credit not categorised Income (e.g. a refund) is never a subscription.
        rows = [_row("50.00", "STREAMCO REFUND", "Groceries", "2026-04")]
        assert build_groups(rows) == {}

    def test_zero_amount_ignored(self):
        rows = [_row("0.00", "STREAMCO", "Subscriptions", "2026-04")]
        assert build_groups(rows) == {}

    def test_multi_hit_month_disqualifies_that_month(self):
        # Grocery pattern: 5 rows of one root in a month -> the month never qualifies.
        rows = [_row("-10.00", "WOOLWORTHS SYDNEY", "Groceries", "2026-04") for _ in range(5)]
        assert build_groups(rows) == {}

    def test_single_hit_month_qualifies_multi_hit_does_not(self):
        rows = [
            _row("-22.99", "STREAMCO", "Subscriptions", "2026-04"),   # qualifies
            _row("-22.99", "STREAMCO", "Subscriptions", "2026-05"),   # two rows ->
            _row("-22.99", "STREAMCO", "Subscriptions", "2026-05"),   # disqualifies 05
        ]
        assert build_groups(rows)[("spend", "STREAMCO")] == {"2026-04": Decimal("22.99")}


# ---------------------------------------------------------------------------
# check_subscriptions orchestration — real in-memory Store + recording send
# ---------------------------------------------------------------------------


class TestCheckSubscriptionsOrchestration:
    def test_bootstrap_first_run_is_silent(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_months(store, "s", "STREAMCO", ["2026-01", "2026-02", "2026-03",
                                                  "2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            sent = check_subscriptions(store, config=_enabled_config())

            assert sent == 0            # bootstrap sends NOTHING
            assert rec.calls == []
            # ...but state was created and the new-slot claimed.
            subs = store.get_subscriptions()
            assert len(subs) == 1
            assert subs[0]["merchant_key"] == "spend:STREAMCO"
            assert subs[0]["status"] == "active"

    def test_bootstrap_then_immediate_rerun_is_still_silent(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            check_subscriptions(store, config=_enabled_config())
            # Second run: not bootstrap, but the new-slot is already claimed.
            assert check_subscriptions(store, config=_enabled_config()) == 0
            assert rec.calls == []

    def test_new_subscription_fires_once_when_not_bootstrap(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            # Bootstrap over STREAMCO first (so has_any_subscriptions -> True).
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            assert check_subscriptions(store, config=_enabled_config()) == 0
            rec.calls.clear()

            # Now a genuinely NEW merchant with a 3-month streak ending at L=2026-06.
            _seed_months(store, "g", "GYMCO", ["2026-04", "2026-05", "2026-06"],
                         "-40.00", category="Subscriptions")
            sent = check_subscriptions(store, config=_enabled_config())

            assert sent == 1
            assert rec.calls == [("subscription_new", 1, None)]

    def test_aggregated_new_count_is_one_send_per_run(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            check_subscriptions(store, config=_enabled_config())  # bootstrap
            rec.calls.clear()

            # TWO new merchants appear in the same run.
            _seed_months(store, "g", "GYMCO", ["2026-04", "2026-05", "2026-06"],
                         "-40.00", category="Subscriptions")
            _seed_months(store, "c", "CLOUDCO", ["2026-04", "2026-05", "2026-06"],
                         "-9.99", category="Subscriptions")
            sent = check_subscriptions(store, config=_enabled_config())

            assert sent == 1  # ONE aggregated send...
            assert rec.calls == [("subscription_new", 2, None)]  # ...with count 2

    def test_price_change_percent_and_direction(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            check_subscriptions(store, config=_enabled_config())  # bootstrap -> expected 22.99
            rec.calls.clear()

            # A 4th month at an out-of-tolerance amount.
            _seed_txn(store, "s7", "2026-07-01", "STREAMCO", "-25.99",
                      "commbank", "Subscriptions", "2026-07")
            sent = check_subscriptions(store, config=_enabled_config())

            assert sent == 1
            # int(3.00 / 22.99 * 100) == 13, direction "up".
            assert rec.calls == [("subscription_price_change", 13, "up")]
            # expected_amount tracked to the new value.
            sub = store.get_subscriptions()[0]
            assert Decimal(sub["expected_amount"]) == Decimal("25.99")

    def test_price_change_claim_once_second_call_silent(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            check_subscriptions(store, config=_enabled_config())
            _seed_txn(store, "s7", "2026-07-01", "STREAMCO", "-25.99",
                      "commbank", "Subscriptions", "2026-07")
            assert check_subscriptions(store, config=_enabled_config()) == 1
            rec.calls.clear()
            # Same month, no new data -> claim-once + in-tolerance -> nothing.
            assert check_subscriptions(store, config=_enabled_config()) == 0
            assert rec.calls == []

    def test_in_tolerance_drift_updates_expected_no_fire(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-14.99", category="Subscriptions")
            check_subscriptions(store, config=_enabled_config())
            rec.calls.clear()
            # 15.49 is within max(1.00, 5%*14.99) of 14.99 -> drift, no alert.
            _seed_txn(store, "s7", "2026-07-01", "STREAMCO", "-15.49",
                      "commbank", "Subscriptions", "2026-07")
            assert check_subscriptions(store, config=_enabled_config()) == 0
            assert rec.calls == []
            sub = store.get_subscriptions()[0]
            assert Decimal(sub["expected_amount"]) == Decimal("15.49")

    def test_missed_income_only_fires_once_a_later_month_exists(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_months(store, "a", "ACME SALARY",
                         ["2026-01", "2026-02", "2026-03", "2026-04"],
                         "5000.00", category="Income")
            check_subscriptions(store, config=_enabled_config())  # bootstrap
            rec.calls.clear()

            # Mid-month export: latest month == the first missed month -> NO fire.
            _seed_txn(store, "f5", "2026-05-01", "FILLERCO", "-3.00",
                      "commbank", "Groceries", "2026-05")
            assert check_subscriptions(store, config=_enabled_config()) == 0
            assert rec.calls == []

            # A strictly-later month arrives -> the miss for 2026-05 fires ONCE.
            _seed_txn(store, "f6", "2026-06-01", "FILLERCO", "-3.00",
                      "commbank", "Groceries", "2026-06")
            sent = check_subscriptions(store, config=_enabled_config())
            assert sent == 1
            assert rec.calls == [("income_missed", None, None)]
            # gap of 2 also flips it ended, quietly.
            sub = next(s for s in store.get_subscriptions()
                       if s["merchant_key"] == "income:ACME SALARY")
            assert sub["status"] == "ended"

    def test_missed_income_claim_once(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_months(store, "a", "ACME SALARY",
                         ["2026-01", "2026-02", "2026-03", "2026-04"],
                         "5000.00", category="Income")
            check_subscriptions(store, config=_enabled_config())
            _seed_txn(store, "f6", "2026-06-01", "FILLERCO", "-3.00",
                      "commbank", "Groceries", "2026-06")
            assert check_subscriptions(store, config=_enabled_config()) == 1
            rec.calls.clear()
            # Second run: slot claimed AND sub already ended -> nothing.
            assert check_subscriptions(store, config=_enabled_config()) == 0
            assert rec.calls == []

    def test_reactivation_is_quiet(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            # Directly seed an ENDED spend subscription.
            store.upsert_subscription(
                merchant_key="spend:STREAMCO", root="STREAMCO", direction="spend",
                expected_amount=Decimal("22.99"), first_seen_month="2026-01",
                last_seen_month="2026-03", status="ended",
            )
            # A qualifying in-tolerance month reappears at the latest month.
            _seed_txn(store, "s6", "2026-06-01", "STREAMCO", "-22.99",
                      "commbank", "Subscriptions", "2026-06")
            sent = check_subscriptions(store, config=_enabled_config())

            assert sent == 0
            assert rec.calls == []
            sub = store.get_subscriptions()[0]
            assert sub["status"] == "active"  # reactivated, silently

    def test_idempotent_second_call_writes_nothing(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            check_subscriptions(store, config=_enabled_config())
            before = store.get_subscriptions()[0]["updated_at"]

            second = check_subscriptions(store, config=_enabled_config())

            assert second == 0
            after = store.get_subscriptions()[0]["updated_at"]
            assert after == before  # guarded upsert wrote NOTHING

    def test_guard_store_raising_returns_zero(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)

        class ExplodingStore:
            def latest_year_month(self):
                raise RuntimeError("synthetic store failure")

        # Must swallow and return 0 — a subscription check never breaks its caller.
        assert check_subscriptions(ExplodingStore(), config=_enabled_config()) == 0
        assert rec.calls == []

    def test_empty_db_returns_zero(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)
        with Store(":memory:") as store:
            assert check_subscriptions(store, config=_enabled_config()) == 0
        assert rec.calls == []


# ---------------------------------------------------------------------------
# Per-type gate + BLOCKING privacy assertion (real notifier, fake pywebpush)
# ---------------------------------------------------------------------------


class TestGateAndPrivacy:
    def test_per_type_toggle_off_suppresses_send(self, monkeypatch):
        """notify:subscription_new = off -> the send is a hard no-op (gate)."""
        captured = _install_capturing_pywebpush(monkeypatch)
        cfg = _enabled_config()
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB)
            store.set_bool_setting("notify:subscription_new", False)
            # Bootstrap STREAMCO, then a NEW merchant that would otherwise fire.
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            check_subscriptions(store, config=cfg)
            _seed_months(store, "g", "GYMCO", ["2026-04", "2026-05", "2026-06"],
                         "-40.00", category="Subscriptions")

            sent = check_subscriptions(store, config=cfg)

            assert sent == 0
            assert captured == []  # gate suppressed delivery

    def test_new_subscription_payload_leaks_nothing(self, monkeypatch):
        captured = _install_capturing_pywebpush(monkeypatch)
        cfg = _enabled_config()
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB)
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            check_subscriptions(store, config=cfg)  # bootstrap, silent
            _seed_months(store, "g", "GYMCO", ["2026-04", "2026-05", "2026-06"],
                         "-40.00", category="Subscriptions")

            check_subscriptions(store, config=cfg)

        _assert_clean_payload(captured, "subscription_new")

    def test_price_change_payload_leaks_nothing(self, monkeypatch):
        captured = _install_capturing_pywebpush(monkeypatch)
        cfg = _enabled_config()
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB)
            _seed_months(store, "s", "STREAMCO", ["2026-04", "2026-05", "2026-06"],
                         "-22.99", category="Subscriptions")
            check_subscriptions(store, config=cfg)
            _seed_txn(store, "s7", "2026-07-01", "STREAMCO", "-25.99",
                      "commbank", "Subscriptions", "2026-07")

            check_subscriptions(store, config=cfg)

        _assert_clean_payload(captured, "subscription_price_change")

    def test_missed_income_payload_leaks_nothing(self, monkeypatch):
        captured = _install_capturing_pywebpush(monkeypatch)
        cfg = _enabled_config()
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB)
            _seed_months(store, "a", "ACME SALARY",
                         ["2026-01", "2026-02", "2026-03", "2026-04"],
                         "5000.00", category="Income")
            check_subscriptions(store, config=cfg)
            _seed_txn(store, "f6", "2026-06-01", "FILLERCO", "-3.00",
                      "commbank", "Groceries", "2026-06")

            check_subscriptions(store, config=cfg)

        _assert_clean_payload(captured, "income_missed")


def _assert_clean_payload(captured: list[str], expected_type: str):
    """BLOCKING: every captured push body carries NO merchant root and NO amount."""
    assert len(captured) == 1, f"expected exactly one {expected_type} send"
    raw = captured[0]
    payload = json.loads(raw)
    assert set(payload.keys()) == {"type", "title", "body"}
    assert payload["type"] == expected_type

    # No merchant roots anywhere in the serialised payload.
    for merchant in ("STREAMCO", "GYMCO", "CLOUDCO", "ACME", "SALARY", "FILLERCO"):
        assert merchant not in raw, f"payload leaked merchant {merchant!r}: {raw!r}"
    # No dollar amounts (the synthetic magnitudes must never surface).
    for amount in ("22.99", "25.99", "40.00", "9.99", "5000"):
        assert amount not in raw, f"payload leaked amount {amount!r}: {raw!r}"
    body = payload["body"]
    assert "$" not in body
    lowered = raw.lower()
    for token in ("balance", "account", "payee", "payer", "description", "merchant"):
        assert token not in lowered


# ---------------------------------------------------------------------------
# Pipeline integration — real run_pipeline with a fake analyser
# ---------------------------------------------------------------------------

# Synthetic CommBank CSV (no header, DD/MM/YYYY, signed amount, description, balance):
# one recurring debit per month for the SAME merchant across three months.
_CB_TEXT = (
    "01/04/2026,-22.99,STREAMCO SUBSCRIPTION,977.01\n"
    "01/05/2026,-22.99,STREAMCO SUBSCRIPTION,954.02\n"
    "01/06/2026,-22.99,STREAMCO SUBSCRIPTION,931.03\n"
)
_CB_BYTES = _CB_TEXT.encode("utf-8")


class _FakeAnalyser:
    """Assigns every row to Subscriptions; records call_count for no-op assertions."""

    def __init__(self):
        self.call_count = 0

    def complete(self, *, system_prompt, user_prompt):
        self.call_count += 1
        items = json.loads(user_prompt)
        return (
            {
                "categories": {str(i["row_index"]): "Subscriptions" for i in items},
                "summary": "Synthetic test summary.",
                "flagged": [],
            },
            "fake-model",
        )


class TestPipelineIntegration:
    def test_pipeline_run_triggers_detection_then_duplicate_is_noop(self, tmp_path, monkeypatch):
        from backend.data_source import Bank
        from backend.pipeline import UploadedFile, run_pipeline

        rec = _RecordingSend()
        monkeypatch.setattr(subscriptions, "send_notification", rec)

        store = Store(":memory:")
        try:
            fake = _FakeAnalyser()
            uploads = [UploadedFile(filename="commbank.csv", bank=Bank.COMMBANK, content=_CB_BYTES)]

            report = run_pipeline(
                uploads, store=store, analyser_client=fake, drive_service=None,
                output_dir=tmp_path, sanitise_log_dir=tmp_path,
            )
            assert report.noop is False
            calls_after_first = fake.call_count

            # Detection ran after ingest: the recurring merchant is now stored state.
            subs = store.get_subscriptions()
            assert len(subs) == 1
            assert subs[0]["merchant_key"] == "spend:STREAMCO SUBSCRIPTION"
            # First run over a fresh DB is a bootstrap -> no push fired.
            assert rec.calls == []

            # Re-upload the SAME file: pipeline no-op path returns BEFORE the check.
            report2 = run_pipeline(
                uploads, store=store, analyser_client=fake, drive_service=None,
                output_dir=tmp_path, sanitise_log_dir=tmp_path,
            )
            assert report2.noop is True
            assert fake.call_count == calls_after_first  # no extra LLM call
            assert rec.calls == []                        # nothing fired
            assert len(store.get_subscriptions()) == 1    # no duplicate state
        finally:
            store.close()
