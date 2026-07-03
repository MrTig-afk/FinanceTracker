"""test_budget_alerts.py — pytest suite for backend/budget_alerts.py (v6 feature 3).

Covers the pure evaluate_budgets threshold math, the check_budget_alerts orchestration
(claim-once semantics, latest-month-only, Transfer exclusion, guard, per-type gate),
the BLOCKING privacy assertion (payload = category name + integer percent ONLY), and
the pipeline/retry integration trigger points.

ALL fixtures use SYNTHETIC data generated in code. No real transactions, no real CSVs.
Every Store is :memory:. NO live network: the notifier is either the default hard
no-op, a monkeypatched capture, or driven with a fake `pywebpush` injected via
sys.modules plus obviously-synthetic (never real) VAPID key strings.
"""
from __future__ import annotations

import json
import sys
import types
from decimal import Decimal

import pytest

import backend.budget_alerts as budget_alerts
from backend.budget_alerts import (
    APPROACHING_THRESHOLD,
    EXCEEDED_THRESHOLD,
    check_budget_alerts,
    evaluate_budgets,
)
from backend.store import Store


# ---------------------------------------------------------------------------
# Synthetic notifier plumbing (mirrors test_notifier.py — no real keys/network)
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


def _seed_txn(store, fp, date, desc, amount, bank, category, ym):
    """Insert one synthetic transaction row directly (bypasses idempotency layer)."""
    store.conn.execute(
        "INSERT INTO transactions"
        "(txn_fingerprint,date,description,amount,bank,category,year_month,created_at)"
        " VALUES (?,?,?,?,?,?,?,'t')",
        (fp, date, desc, amount, bank, category, ym),
    )
    store.conn.commit()


class _RecordingSend:
    """Callable stand-in for send_notification recording (ntype, count, detail)."""

    def __init__(self):
        self.calls: list[tuple[str, int | None, str | None]] = []

    def __call__(self, store, ntype, *, count=None, detail=None, config=None):
        self.calls.append((ntype, count, detail))
        return 1  # simulate one successful delivery


# ---------------------------------------------------------------------------
# evaluate_budgets — pure threshold math (no store, no network)
# ---------------------------------------------------------------------------


class TestEvaluateBudgets:
    def test_below_80_no_alert(self):
        assert evaluate_budgets({"Groceries": Decimal("100")}, {"Groceries": "-79"}) == []

    def test_exactly_80_decimal_exact(self):
        # 240 / 300 = exactly 80 (Decimal math, not float rounding to 79).
        out = evaluate_budgets({"Groceries": Decimal("300")}, {"Groceries": "-240.00"})
        assert out == [("Groceries", 80, 80)]

    def test_99_percent_approaching_only(self):
        out = evaluate_budgets({"Groceries": Decimal("100")}, {"Groceries": "-99"})
        assert out == [("Groceries", 80, 99)]

    def test_exactly_100_claims_both_thresholds(self):
        out = evaluate_budgets({"Groceries": Decimal("100")}, {"Groceries": "-100"})
        assert out == [("Groceries", 80, 100), ("Groceries", 100, 100)]

    def test_150_percent_claims_both(self):
        out = evaluate_budgets({"Groceries": Decimal("100")}, {"Groceries": "-150"})
        assert out == [("Groceries", 80, 150), ("Groceries", 100, 150)]

    def test_net_credit_category_no_alert(self):
        # A net-positive total (refund month) → spend 0 → nothing crosses.
        assert evaluate_budgets({"Groceries": Decimal("100")}, {"Groceries": "50"}) == []

    def test_missing_total_treated_as_zero(self):
        assert evaluate_budgets({"Groceries": Decimal("100")}, {}) == []

    def test_unbudgeted_categories_ignored(self):
        # Housing spend is present but only Groceries carries a budget.
        out = evaluate_budgets(
            {"Groceries": Decimal("100")},
            {"Groceries": "-40", "Housing": "-9999"},
        )
        assert out == []

    def test_income_key_never_evaluated(self):
        # Income is not budgetable — even if it sneaks into the budgets dict it is skipped.
        assert evaluate_budgets({"Income": Decimal("100")}, {"Income": "-500"}) == []

    def test_transfer_key_never_evaluated(self):
        assert evaluate_budgets({"Transfer": Decimal("100")}, {"Transfer": "-500"}) == []

    def test_uncategorised_key_never_evaluated(self):
        assert evaluate_budgets(
            {"Uncategorised": Decimal("100")}, {"Uncategorised": "-500"}
        ) == []

    def test_zero_budget_skipped(self):
        assert evaluate_budgets({"Groceries": Decimal("0")}, {"Groceries": "-50"}) == []

    def test_negative_budget_skipped(self):
        assert evaluate_budgets({"Groceries": Decimal("-100")}, {"Groceries": "-50"}) == []

    def test_percent_is_floored(self):
        # 2/3 * 100 = 66.66… floors to 66 (still below 80 → no alert), proving int() floor.
        assert evaluate_budgets({"Transport": Decimal("3")}, {"Transport": "-2"}) == []
        # 89/100 floors to 89 → approaching.
        assert evaluate_budgets({"Transport": Decimal("100")}, {"Transport": "-89.99"}) == [
            ("Transport", 80, 89)
        ]

    def test_threshold_constants(self):
        assert APPROACHING_THRESHOLD == 80
        assert EXCEEDED_THRESHOLD == 100


# ---------------------------------------------------------------------------
# check_budget_alerts — orchestration with a real in-memory Store + capture
# ---------------------------------------------------------------------------


class TestCheckBudgetAlerts:
    def test_crossing_80_sends_one_approaching(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            _seed_txn(store, "g1", "2026-06-01", "SYNTH GROCER", "-240.00",
                      "commbank", "Groceries", "2026-06")

            sent = check_budget_alerts(store)

        assert sent == 1
        assert rec.calls == [("budget_approaching", 80, "Groceries")]

    def test_jump_past_100_sends_only_exceeded_but_claims_both(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            _seed_txn(store, "g1", "2026-06-01", "SYNTH GROCER", "-340.00",
                      "commbank", "Groceries", "2026-06")

            sent = check_budget_alerts(store)

            # Only the exceeded notification is sent (the 80 slot is claimed silently).
            assert sent == 1
            assert rec.calls == [("budget_exceeded", 113, "Groceries")]
            # BOTH claim rows exist in the fired-state table.
            rows = store.conn.execute(
                "SELECT threshold FROM budget_alert_fired "
                "WHERE category='Groceries' AND year_month='2026-06' ORDER BY threshold"
            ).fetchall()
            assert [r["threshold"] for r in rows] == [80, 100]

    def test_second_run_unchanged_is_noop(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            _seed_txn(store, "g1", "2026-06-01", "SYNTH GROCER", "-240.00",
                      "commbank", "Groceries", "2026-06")

            first = check_budget_alerts(store)
            second = check_budget_alerts(store)

        assert first == 1
        assert second == 0  # claims block the re-fire
        assert len(rec.calls) == 1

    def test_transfer_rows_excluded_from_spend(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            # A small grocery spend (under 80%) plus a large Transfer debit that,
            # if counted, would blow the budget. Transfer rows are excluded from totals.
            _seed_txn(store, "g1", "2026-06-01", "SYNTH GROCER", "-50.00",
                      "commbank", "Groceries", "2026-06")
            _seed_txn(store, "t1", "2026-06-02", "SYNTH XFER", "-5000.00",
                      "commbank", "Transfer", "2026-06")

            sent = check_budget_alerts(store)

        assert sent == 0
        assert rec.calls == []

    def test_latest_month_only(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            # An old month over budget — must NOT fire (only the latest month is checked).
            _seed_txn(store, "old", "2026-05-01", "SYNTH OLD", "-999.00",
                      "commbank", "Groceries", "2026-05")
            # The latest month is under 80%.
            _seed_txn(store, "new", "2026-06-01", "SYNTH NEW", "-10.00",
                      "commbank", "Groceries", "2026-06")

            sent = check_budget_alerts(store)

        assert sent == 0
        assert rec.calls == []

    def test_empty_budgets_returns_zero(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)
        with Store(":memory:") as store:
            _seed_txn(store, "g1", "2026-06-01", "SYNTH GROCER", "-240.00",
                      "commbank", "Groceries", "2026-06")
            assert check_budget_alerts(store) == 0
        assert rec.calls == []

    def test_empty_db_returns_zero(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)
        with Store(":memory:") as store:
            store.set_budget("Groceries", Decimal("300"))
            # No transactions at all → latest_year_month() is None.
            assert check_budget_alerts(store) == 0
        assert rec.calls == []

    def test_guard_store_raising_returns_zero(self, monkeypatch):
        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)

        class ExplodingStore:
            def get_budgets(self):
                raise RuntimeError("synthetic store failure")

        # Must swallow the error and return 0 — never break the caller.
        assert check_budget_alerts(ExplodingStore()) == 0
        assert rec.calls == []


# ---------------------------------------------------------------------------
# BLOCKING privacy assertion + real per-type gate (real notifier, fake pywebpush)
# ---------------------------------------------------------------------------


class TestPrivacyAndGate:
    def test_payload_carries_category_and_percent_only(self, monkeypatch):
        """BLOCKING: the delivered push body has the category name + integer percent
        ONLY — no dollar amounts, no descriptions, no balances/account markers."""
        captured = _install_capturing_pywebpush(monkeypatch)
        cfg = _enabled_config()
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB)
            store.set_budget("Groceries", Decimal("300"))
            # -240.00 → exactly 80%. The dollar figures below must NEVER surface.
            _seed_txn(store, "g1", "2026-06-01", "SECRET MERCHANT NAME", "-240.00",
                      "commbank", "Groceries", "2026-06")

            sent = check_budget_alerts(store, config=cfg)

        assert sent == 1
        assert len(captured) == 1
        payload = json.loads(captured[0])
        assert set(payload.keys()) == {"type", "title", "body"}
        assert payload["type"] == "budget_approaching"

        body = payload["body"]
        # Category name + percent are allowed.
        assert "Groceries" in body
        assert "80" in body
        # No dollar sign, no leaked amounts, no leaked description.
        assert "$" not in body
        assert "240" not in body
        assert "SECRET MERCHANT NAME" not in captured[0]
        lowered = captured[0].lower()
        for token in ("balance", "account", "payee", "payer", "description", "merchant"):
            assert token not in lowered
        # The only digits in the body are the percent (80).
        digits = "".join(ch for ch in body if ch.isdigit())
        assert digits == "80"

    def test_exceeded_payload_body_shape(self, monkeypatch):
        captured = _install_capturing_pywebpush(monkeypatch)
        cfg = _enabled_config()
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB)
            store.set_budget("Transport", Decimal("100"))
            _seed_txn(store, "x1", "2026-06-01", "SYNTH BUS", "-150.00",
                      "commbank", "Transport", "2026-06")

            check_budget_alerts(store, config=cfg)

        payload = json.loads(captured[0])
        assert payload["type"] == "budget_exceeded"
        assert "Transport" in payload["body"]
        assert "$" not in payload["body"]
        # Only the percent (150) appears as digits.
        assert "".join(ch for ch in payload["body"] if ch.isdigit()) == "150"

    def test_per_type_toggle_off_claims_but_does_not_send(self, monkeypatch):
        """notify:budget_exceeded off → the slot is still claimed, but the per-type gate
        in send_notification suppresses delivery (returns 0)."""
        captured = _install_capturing_pywebpush(monkeypatch)
        cfg = _enabled_config()
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB)
            store.set_bool_setting("notify:budget_exceeded", False)
            store.set_budget("Groceries", Decimal("100"))
            _seed_txn(store, "g1", "2026-06-01", "SYNTH GROCER", "-150.00",
                      "commbank", "Groceries", "2026-06")

            sent = check_budget_alerts(store, config=cfg)

            # Gate suppressed the exceeded delivery → nothing pushed.
            assert sent == 0
            assert captured == []
            # But both slots were still CLAIMED (so a future re-check never double-fires).
            rows = store.conn.execute(
                "SELECT threshold FROM budget_alert_fired "
                "WHERE category='Groceries' ORDER BY threshold"
            ).fetchall()
            assert [r["threshold"] for r in rows] == [80, 100]

    def test_default_push_disabled_is_hard_noop(self):
        """With no config (push disabled by default) delivery is a hard no-op even
        though the thresholds are crossed and claims are recorded."""
        with Store(":memory:") as store:
            store.upsert_push_subscription(_SYNTH_SUB)
            store.set_budget("Groceries", Decimal("100"))
            _seed_txn(store, "g1", "2026-06-01", "SYNTH GROCER", "-90.00",
                      "commbank", "Groceries", "2026-06")

            # No config passed → push_config() reads the neutralised env → disabled.
            sent = check_budget_alerts(store)

        assert sent == 0


# ---------------------------------------------------------------------------
# Pipeline / retry integration — real run_pipeline with a fake analyser
# ---------------------------------------------------------------------------

# Synthetic CommBank CSV (no header, DD/MM/YYYY, signed amount, description, balance):
# debits only, in a fresh month, so the (Groceries) category sum is clearly negative.
_CB_TEXT = (
    "01/07/2026,-240.00,SYNTH GROCER ONE,760.00\n"
    "02/07/2026,-30.00,SYNTH GROCER TWO,730.00\n"
)
_CB_BYTES = _CB_TEXT.encode("utf-8")


class _FakeAnalyser:
    """Assigns every row to Groceries; records call_count for no-op assertions."""

    def __init__(self):
        self.call_count = 0

    def complete(self, *, system_prompt, user_prompt):
        self.call_count += 1
        items = json.loads(user_prompt)
        return (
            {
                "categories": {str(i["row_index"]): "Groceries" for i in items},
                "summary": "Synthetic test summary.",
                "flagged": [],
            },
            "fake-model",
        )


class TestPipelineIntegration:
    def test_pipeline_run_fires_budget_alert_once(self, tmp_path, monkeypatch):
        from backend.data_source import Bank
        from backend.pipeline import UploadedFile, run_pipeline

        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)

        store = Store(":memory:")
        try:
            # Budget below the -270 total (240+30) → crosses 80% (and 100%).
            store.set_budget("Groceries", Decimal("300"))
            fake = _FakeAnalyser()
            uploads = [UploadedFile(filename="commbank.csv", bank=Bank.COMMBANK, content=_CB_BYTES)]

            report = run_pipeline(
                uploads, store=store, analyser_client=fake, drive_service=None,
                output_dir=tmp_path, sanitise_log_dir=tmp_path,
            )
            assert report.noop is False
            calls_after_first = fake.call_count

            # 270/300 = 90 → approaching only.
            assert rec.calls == [("budget_approaching", 90, "Groceries")]

            # Re-run the SAME file: pipeline no-op path returns early BEFORE the check.
            rec.calls.clear()
            report2 = run_pipeline(
                uploads, store=store, analyser_client=fake, drive_service=None,
                output_dir=tmp_path, sanitise_log_dir=tmp_path,
            )
            assert report2.noop is True
            # No additional LLM call and no additional budget send on the no-op re-run.
            assert fake.call_count == calls_after_first
            assert rec.calls == []
        finally:
            store.close()

    def test_retry_uncategorised_success_fires(self, tmp_path, monkeypatch):
        from backend.pipeline import retry_uncategorised

        rec = _RecordingSend()
        monkeypatch.setattr(budget_alerts, "send_notification", rec)

        store = Store(":memory:")
        try:
            store.set_budget("Groceries", Decimal("300"))
            # Uncategorised rows in the latest month; the fake analyser will sort them
            # into Groceries, pushing the category over 80%.
            _seed_txn(store, "u1", "2026-07-01", "SYNTH GROCER ONE", "-240.00",
                      "commbank", None, "2026-07")
            _seed_txn(store, "u2", "2026-07-02", "SYNTH GROCER TWO", "-30.00",
                      "commbank", None, "2026-07")
            fake = _FakeAnalyser()

            outcome = retry_uncategorised(
                store, analyser_client=fake, drive_service=None,
                output_dir=tmp_path, sanitise_log_dir=tmp_path,
            )

            assert outcome["ok"] is True
            assert rec.calls == [("budget_approaching", 90, "Groceries")]
        finally:
            store.close()
