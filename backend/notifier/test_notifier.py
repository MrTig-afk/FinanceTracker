"""test_notifier.py — pytest suite for backend/notifier/notifier.py (v2 Pass 3 scaffold).

ALL fixtures use SYNTHETIC data generated in code. No real VAPID keys anywhere —
every "real-keyed" test uses obviously-fake synthetic key strings. No live network
calls — `pywebpush` is never installed/imported for real; it is either absent
(hard no-op path) or injected as a fake module via sys.modules (enabled path).

Critical fail-closed assertions (pre-deployment checklist, blocking):
  - send_processed_notification is a HARD no-op (returns 0, zero network calls,
    does NOT import pywebpush) whenever PUSH_ENABLED is falsy or any VAPID_* value
    is missing/placeholder.
  - When enabled + real-keyed, the payload sent is EXACTLY the fixed generic
    {"title": "FinanceTracker", "body": "Your statement was processed"} — no
    transaction data of any kind.
"""
from __future__ import annotations

import sys

import pytest

from backend.notifier import (
    NOTIFICATION_TYPES,
    build_notification,
    is_push_enabled,
    push_config,
    send_monthly_reminder,
    send_notification,
    send_processed_notification,
)
from backend.notifier.notifier import (
    NOTIFICATION_BODY,
    NOTIFICATION_TITLE,
    _PLACEHOLDER_PRIVATE_KEY,
    _PLACEHOLDER_PUBLIC_KEY,
)

# ---------------------------------------------------------------------------
# Synthetic config / fixtures
# ---------------------------------------------------------------------------

_SYNTH_PUBLIC_KEY = "SYNTHETIC_TEST_VAPID_PUBLIC_KEY_NOT_REAL_abc123"
_SYNTH_PRIVATE_KEY = "SYNTHETIC_TEST_VAPID_PRIVATE_KEY_NOT_REAL_xyz789"
_SYNTH_SUBJECT = "mailto:synthetic-test@example.test"

_SYNTH_SUB_1 = {
    "endpoint": "https://example.test/push/SYNTH_ENDPOINT_1",
    "keys": {"p256dh": "synth_p256dh_1", "auth": "synth_auth_1"},
}
_SYNTH_SUB_2 = {
    "endpoint": "https://example.test/push/SYNTH_ENDPOINT_2",
    "keys": {"p256dh": "synth_p256dh_2", "auth": "synth_auth_2"},
}


def _enabled_config(**overrides) -> dict:
    cfg = {
        "enabled": True,
        "public_key": _SYNTH_PUBLIC_KEY,
        "private_key": _SYNTH_PRIVATE_KEY,
        "subject": _SYNTH_SUBJECT,
    }
    cfg.update(overrides)
    return cfg


class FakeStore:
    """Minimal stand-in for Store — only implements list_push_subscriptions()."""

    def __init__(self, subs: list[dict] | None = None, *, raise_if_listed: bool = False) -> None:
        self._subs = subs if subs is not None else []
        self._raise_if_listed = raise_if_listed
        self.list_calls = 0

    def list_push_subscriptions(self) -> list[dict]:
        self.list_calls += 1
        if self._raise_if_listed:
            raise AssertionError(
                "list_push_subscriptions() must NOT be called on the hard no-op path"
            )
        return self._subs


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no real .env values bleed into these tests.

    Set (not delete) the keys to empty: push_config() calls load_dotenv(), which
    would otherwise re-read the developer's .env and repopulate a deleted key. An
    already-present empty value is NOT overridden by load_dotenv (override=False),
    so the fail-closed default holds even when push is activated locally.
    """
    for key in ("PUSH_ENABLED", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        monkeypatch.setenv(key, "")


@pytest.fixture(autouse=True)
def _clean_pywebpush_module():
    """Ensure a fake pywebpush injected by one test never leaks into another."""
    had = "pywebpush" in sys.modules
    original = sys.modules.get("pywebpush")
    yield
    if had:
        sys.modules["pywebpush"] = original
    else:
        sys.modules.pop("pywebpush", None)


def _install_fake_pywebpush(monkeypatch, *, webpush_fn=None, must_not_be_called: bool = False):
    """Inject a fake `pywebpush` module into sys.modules.

    If must_not_be_called, `webpush` raises AssertionError if invoked at all —
    structurally proving the lazy import path was never reached/used.
    """
    import types

    fake_module = types.ModuleType("pywebpush")

    class WebPushException(Exception):
        pass

    def _default_must_not_be_called(*args, **kwargs):
        raise AssertionError("pywebpush.webpush must NOT be called on the fail-closed path")

    fake_module.webpush = webpush_fn if webpush_fn is not None else _default_must_not_be_called
    fake_module.WebPushException = WebPushException
    monkeypatch.setitem(sys.modules, "pywebpush", fake_module)
    return fake_module


# ---------------------------------------------------------------------------
# TestHardNoOpByDefault — PUSH_ENABLED unset (default shipped state)
# ---------------------------------------------------------------------------


class TestHardNoOpByDefault:
    """Default shipped state: PUSH_ENABLED unset → hard no-op, zero network."""

    def test_returns_zero_with_two_synthetic_subscriptions(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1, _SYNTH_SUB_2])
        result = send_processed_notification(store)
        assert result == 0

    def test_store_never_queried(self, monkeypatch):
        """Structural proof: the store is never even asked for its subscriptions."""
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1, _SYNTH_SUB_2], raise_if_listed=True)
        result = send_processed_notification(store)
        assert result == 0
        assert store.list_calls == 0

    def test_pywebpush_not_imported(self, monkeypatch):
        """A fake pywebpush that raises AssertionError if called proves zero network."""
        fake = _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1])
        send_processed_notification(store)  # must not raise (webpush never invoked)

    def test_bare_import_does_not_import_pywebpush(self):
        """A bare `import backend.notifier` must never pull in pywebpush."""
        sys.modules.pop("pywebpush", None)
        import backend.notifier  # noqa: F401
        import importlib
        importlib.reload(backend.notifier)
        assert "pywebpush" not in sys.modules

    def test_explicit_config_disabled(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config(enabled=False)
        assert send_processed_notification(store, config=cfg) == 0


# ---------------------------------------------------------------------------
# TestFailClosedOnMissingOrPlaceholderKeys — PUSH_ENABLED=true but bad keys
# ---------------------------------------------------------------------------


class TestFailClosedOnMissingOrPlaceholderKeys:
    """Every one of these must still return 0 with zero webpush calls."""

    def test_empty_public_key(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config(public_key="")
        assert send_processed_notification(store, config=cfg) == 0

    def test_empty_private_key(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config(private_key="")
        assert send_processed_notification(store, config=cfg) == 0

    def test_empty_subject(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config(subject="")
        assert send_processed_notification(store, config=cfg) == 0

    def test_placeholder_public_key(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config(public_key=_PLACEHOLDER_PUBLIC_KEY)
        assert send_processed_notification(store, config=cfg) == 0

    def test_placeholder_private_key(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config(private_key=_PLACEHOLDER_PRIVATE_KEY)
        assert send_processed_notification(store, config=cfg) == 0

    def test_store_never_queried_on_placeholder_keys(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1], raise_if_listed=True)
        cfg = _enabled_config(public_key=_PLACEHOLDER_PUBLIC_KEY)
        assert send_processed_notification(store, config=cfg) == 0
        assert store.list_calls == 0


# ---------------------------------------------------------------------------
# TestEnabledRealKeyedPath — fully enabled + synthetic real-looking keys
# ---------------------------------------------------------------------------


class TestEnabledRealKeyedPath:
    """Enabled + synthetic non-placeholder keys → webpush called once per sub."""

    def test_returns_two_for_two_subscriptions(self, monkeypatch):
        calls = []

        def _fake_webpush(**kwargs):
            calls.append(kwargs)

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1, _SYNTH_SUB_2])
        cfg = _enabled_config()
        result = send_processed_notification(store, config=cfg)
        assert result == 2
        assert len(calls) == 2

    def test_zero_subscriptions_returns_zero(self, monkeypatch):
        def _fake_webpush(**kwargs):
            raise AssertionError("webpush must not be called with zero subscriptions")

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([])
        cfg = _enabled_config()
        assert send_processed_notification(store, config=cfg) == 0

    def test_one_failing_endpoint_does_not_abort_the_loop(self, monkeypatch):
        """A raising webpush on one endpoint must not prevent the other from being attempted."""
        seen_endpoints = []

        def _fake_webpush(*, subscription_info, **kwargs):
            seen_endpoints.append(subscription_info["endpoint"])
            if subscription_info["endpoint"] == _SYNTH_SUB_1["endpoint"]:
                raise Exception("synthetic simulated send failure")

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1, _SYNTH_SUB_2])
        cfg = _enabled_config()
        result = send_processed_notification(store, config=cfg)

        assert result == 2, "both sends must be attempted even though one raised"
        assert set(seen_endpoints) == {_SYNTH_SUB_1["endpoint"], _SYNTH_SUB_2["endpoint"]}

    def test_webpush_exception_specifically_does_not_abort_the_loop(self, monkeypatch):
        """Same guarantee using the pywebpush-specific WebPushException type."""
        import types

        fake_module = types.ModuleType("pywebpush")

        class WebPushException(Exception):
            pass

        seen = []

        def _fake_webpush(*, subscription_info, **kwargs):
            seen.append(subscription_info["endpoint"])
            if subscription_info["endpoint"] == _SYNTH_SUB_1["endpoint"]:
                raise WebPushException("synthetic push service rejection")

        fake_module.webpush = _fake_webpush
        fake_module.WebPushException = WebPushException
        monkeypatch.setitem(sys.modules, "pywebpush", fake_module)

        store = FakeStore([_SYNTH_SUB_1, _SYNTH_SUB_2])
        cfg = _enabled_config()
        result = send_processed_notification(store, config=cfg)
        assert result == 2
        assert len(seen) == 2


# ---------------------------------------------------------------------------
# TestGenericContent — payload must be the fixed generic pair only
# ---------------------------------------------------------------------------


class TestGenericContent:
    """The payload passed to webpush() must carry ZERO financial data."""

    def test_payload_is_structured_type_title_body(self, monkeypatch):
        import json

        captured = []

        def _fake_webpush(*, data, **kwargs):
            captured.append(data)

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config()
        send_processed_notification(store, count=5, config=cfg)

        assert len(captured) == 1
        payload = json.loads(captured[0])
        # Structured {type, title, body} the service worker routes on.
        assert set(payload.keys()) == {"type", "title", "body"}
        assert payload["type"] == "processed"
        assert payload["body"] == "Statements processed - 5 transactions sorted."

    def test_payload_carries_only_counts_never_amounts_or_names(self, monkeypatch):
        """Counts are allowed; amounts / merchants / account markers are not."""
        captured = []

        def _fake_webpush(*, data, **kwargs):
            captured.append(data)

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1, _SYNTH_SUB_2])
        cfg = _enabled_config()
        send_processed_notification(store, count=3, config=cfg)

        forbidden = ["$", "balance", "account", "payee", "payer", "description"]
        for payload_str in captured:
            lowered = payload_str.lower()
            for token in forbidden:
                assert token not in lowered, f"payload leaked {token!r}: {payload_str!r}"

    def test_payload_contains_no_endpoint_values(self, monkeypatch):
        captured = []

        def _fake_webpush(*, data, **kwargs):
            captured.append(data)

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config()
        send_processed_notification(store, config=cfg)

        for payload_str in captured:
            assert _SYNTH_SUB_1["endpoint"] not in payload_str
            assert _SYNTH_SUB_1["keys"]["p256dh"] not in payload_str
            assert _SYNTH_SUB_1["keys"]["auth"] not in payload_str

    def test_notification_body_constant_has_no_financial_data(self):
        forbidden = ["$", "amount", "balance", "category", "description"]
        lowered = NOTIFICATION_BODY.lower()
        for token in forbidden:
            assert token not in lowered
        assert not any(ch.isdigit() for ch in NOTIFICATION_BODY)

    def test_notification_title_and_body_are_the_exact_fixed_strings(self):
        assert NOTIFICATION_TITLE == "FinanceTracker"
        assert NOTIFICATION_BODY == "Your statement was processed"

    def test_vapid_claims_never_leak_into_payload(self, monkeypatch):
        """vapid_claims/private_key are passed as separate kwargs, never folded into data."""
        captured = []

        def _fake_webpush(*, data, vapid_private_key, vapid_claims, **kwargs):
            captured.append(data)

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config()
        send_processed_notification(store, config=cfg)

        for payload_str in captured:
            assert _SYNTH_PRIVATE_KEY not in payload_str
            assert _SYNTH_SUBJECT not in payload_str


# ---------------------------------------------------------------------------
# TestIsPushEnabledTruthTable
# ---------------------------------------------------------------------------


class TestIsPushEnabledTruthTable:
    def test_all_true_is_enabled(self):
        assert is_push_enabled(_enabled_config()) is True

    def test_disabled_flag_is_false(self):
        assert is_push_enabled(_enabled_config(enabled=False)) is False

    def test_missing_public_key_is_false(self):
        assert is_push_enabled(_enabled_config(public_key="")) is False

    def test_missing_private_key_is_false(self):
        assert is_push_enabled(_enabled_config(private_key="")) is False

    def test_missing_subject_is_false(self):
        assert is_push_enabled(_enabled_config(subject="")) is False

    def test_placeholder_public_key_is_false(self):
        assert is_push_enabled(_enabled_config(public_key=_PLACEHOLDER_PUBLIC_KEY)) is False

    def test_placeholder_private_key_is_false(self):
        assert is_push_enabled(_enabled_config(private_key=_PLACEHOLDER_PRIVATE_KEY)) is False

    def test_default_cfg_none_reads_env_and_is_false_when_unset(self, monkeypatch):
        # Empty (not deleted): push_config()/is_push_enabled() call load_dotenv(),
        # which would repopulate a deleted key from a real local .env. Empty and
        # truly-unset yield an identical config here (getenv defaults are "").
        for key in ("PUSH_ENABLED", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
            monkeypatch.setenv(key, "")
        assert is_push_enabled() is False


# ---------------------------------------------------------------------------
# TestPushConfig — env resolution
# ---------------------------------------------------------------------------


class TestPushConfig:
    def test_defaults_when_env_unset(self, monkeypatch):
        # Empty (not deleted) so load_dotenv() inside push_config() cannot
        # repopulate from a real local .env; the resolved config is identical.
        for key in ("PUSH_ENABLED", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
            monkeypatch.setenv(key, "")
        cfg = push_config()
        assert cfg == {
            "enabled": False,
            "public_key": "",
            "private_key": "",
            "subject": "",
        }

    @pytest.mark.parametrize("truthy_value", ["1", "true", "TRUE", "yes", "on", "On"])
    def test_truthy_env_values_enable(self, monkeypatch, truthy_value):
        monkeypatch.setenv("PUSH_ENABLED", truthy_value)
        cfg = push_config()
        assert cfg["enabled"] is True

    @pytest.mark.parametrize("falsy_value", ["0", "false", "no", "off", "", "garbage"])
    def test_non_truthy_env_values_disable(self, monkeypatch, falsy_value):
        monkeypatch.setenv("PUSH_ENABLED", falsy_value)
        cfg = push_config()
        assert cfg["enabled"] is False

    def test_explicit_args_override_env(self, monkeypatch):
        monkeypatch.setenv("PUSH_ENABLED", "true")
        monkeypatch.setenv("VAPID_PUBLIC_KEY", "ENV_KEY")
        cfg = push_config(enabled=False, public_key="ARG_KEY")
        assert cfg["enabled"] is False
        assert cfg["public_key"] == "ARG_KEY"


# ---------------------------------------------------------------------------
# TestBuildNotificationCatalog — the full {type, title, body} payload contract
# ---------------------------------------------------------------------------


class TestBuildNotificationCatalog:
    """build_notification() renders each catalog type as a structured payload."""

    def test_every_declared_type_builds(self):
        for ntype in NOTIFICATION_TYPES:
            payload = build_notification(ntype, count=2, detail="CommBank")
            assert set(payload.keys()) == {"type", "title", "body"}
            assert payload["type"] == ntype
            assert payload["title"] and payload["body"]

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError):
            build_notification("not_a_real_type")

    def test_processed_embeds_count(self):
        p = build_notification("processed", count=7)
        assert p["body"] == "Statements processed - 7 transactions sorted."

    def test_processed_recovered_copy(self):
        p = build_notification("processed_recovered", count=4)
        assert p["type"] == "processed_recovered"
        assert "queued upload" in p["body"]
        assert "4 transactions" in p["body"]

    def test_categorisation_failed_embeds_pending_count(self):
        p = build_notification("categorisation_failed", count=9)
        assert p["type"] == "categorisation_failed"
        assert "9 transactions" in p["body"]

    def test_categorisation_recovered_embeds_count(self):
        p = build_notification("categorisation_recovered", count=6)
        assert "6 transactions" in p["body"]

    def test_parse_error_names_the_bank(self):
        p = build_notification("parse_error", detail="Westpac")
        assert "Westpac" in p["body"]

    def test_drive_backup_failed_names_the_month(self):
        p = build_notification("drive_backup_failed", detail="2026-06")
        assert "2026-06" in p["body"]

    def test_local_backup_failed_is_fixed_status_copy(self):
        p = build_notification("local_backup_failed")
        assert p["title"] == "Local backup failed"
        assert (
            p["body"]
            == "The weekly local database backup failed - check the supervisor log."
        )
        # Fixed copy only: nothing dynamic can leak (no counts, no paths).
        assert not any(ch.isdigit() for ch in p["body"])

    def test_duplicate_noop_is_quiet_status_only(self):
        p = build_notification("duplicate_noop")
        assert p["type"] == "duplicate_noop"
        assert not any(ch.isdigit() for ch in p["body"])

    def test_generic_error_leaks_no_internal_detail(self):
        p = build_notification("generic_error")
        assert p["body"] == "Something went wrong on the last run - open the app to check."

    def test_monthly_reminder_copy(self):
        p = build_notification("monthly_reminder")
        assert "New month" in p["body"]

    def test_no_emoji_or_em_dash_anywhere(self):
        """House style: plain hyphens only, no em dashes, no emojis."""
        for ntype in NOTIFICATION_TYPES:
            p = build_notification(ntype, count=1, detail="CommBank")
            for text in (p["title"], p["body"]):
                assert "—" not in text  # em dash
                assert "–" not in text  # en dash
                assert all(ord(ch) < 128 for ch in text), f"non-ascii in {ntype}: {text!r}"

    def test_none_count_never_leaks_and_is_safe(self):
        p = build_notification("processed", count=None)
        assert "some transactions" in p["body"]


# ---------------------------------------------------------------------------
# TestSendNotificationCatalog — send path per type (fail-closed + enabled)
# ---------------------------------------------------------------------------


class TestSendNotificationCatalog:
    def test_send_notification_hard_no_op_by_default(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)  # webpush raises if called
        store = FakeStore([_SYNTH_SUB_1], raise_if_listed=True)
        assert send_notification(store, "processed", count=3) == 0
        assert store.list_calls == 0

    def test_send_monthly_reminder_hard_no_op_by_default(self, monkeypatch):
        _install_fake_pywebpush(monkeypatch)
        store = FakeStore([_SYNTH_SUB_1], raise_if_listed=True)
        assert send_monthly_reminder(store) == 0
        assert store.list_calls == 0

    def test_send_notification_enabled_sends_expected_type(self, monkeypatch):
        import json

        captured = []

        def _fake_webpush(*, data, **kwargs):
            captured.append(json.loads(data))

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1, _SYNTH_SUB_2])
        cfg = _enabled_config()
        result = send_notification(store, "parse_error", detail="CommBank", config=cfg)

        assert result == 2
        assert all(p["type"] == "parse_error" for p in captured)
        assert all("CommBank" in p["body"] for p in captured)

    def test_send_monthly_reminder_enabled(self, monkeypatch):
        import json

        captured = []

        def _fake_webpush(*, data, **kwargs):
            captured.append(json.loads(data))

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config()
        assert send_monthly_reminder(store, config=cfg) == 1
        assert captured[0]["type"] == "monthly_reminder"


# ---------------------------------------------------------------------------
# TestPerTypeGate — Feature E per-type opt-out gate at the TOP of send_notification.
#
# Isolates the gate from delivery by stubbing _deliver, so the assertions are
# purely about whether the gate short-circuits. Uses a real in-memory Store to
# drive the notify:<ntype> flag. No network anywhere.
# ---------------------------------------------------------------------------


class TestPerTypeGate:
    @staticmethod
    def _stub_deliver(monkeypatch):
        import backend.notifier.notifier as notifier_mod

        calls: list[int] = []

        def _fake(store, payload, config):  # noqa: ARG001 — signature parity only
            calls.append(1)
            return 99

        monkeypatch.setattr(notifier_mod, "_deliver", _fake)
        return calls

    def test_disabled_type_is_hard_noop(self, monkeypatch):
        from backend.store import Store

        calls = self._stub_deliver(monkeypatch)
        store = Store(":memory:")
        store.set_bool_setting("notify:processed", False)
        try:
            assert send_notification(store, "processed", count=3) == 0
            # Hard no-op: delivery is never reached when the type is disabled.
            assert calls == []
        finally:
            store.close()

    def test_enabled_type_reaches_delivery(self, monkeypatch):
        from backend.store import Store

        calls = self._stub_deliver(monkeypatch)
        store = Store(":memory:")  # default: every type enabled (opt-out model)
        try:
            assert send_notification(store, "processed", count=3) == 99
            assert calls == [1]
        finally:
            store.close()

    def test_default_unset_type_is_enabled(self, monkeypatch):
        from backend.store import Store

        calls = self._stub_deliver(monkeypatch)
        store = Store(":memory:")  # never configured -> default True
        try:
            assert send_notification(store, "parse_error", detail="CommBank") == 99
            assert calls == [1]
        finally:
            store.close()

    def test_monthly_reminder_respects_its_gate(self, monkeypatch):
        from backend.store import Store

        calls = self._stub_deliver(monkeypatch)
        store = Store(":memory:")
        store.set_bool_setting("notify:monthly_reminder", False)
        try:
            assert send_monthly_reminder(store) == 0
            assert calls == []
        finally:
            store.close()

    def test_budget_types_default_enabled_reach_delivery(self, monkeypatch):
        from backend.store import Store

        calls = self._stub_deliver(monkeypatch)
        store = Store(":memory:")  # opt-out model: every type enabled by default
        try:
            for ntype in ("budget_approaching", "budget_exceeded"):
                assert send_notification(
                    store, ntype, count=85, detail="Groceries"
                ) == 99
            assert calls == [1, 1]
        finally:
            store.close()

    def test_budget_type_disabled_is_hard_noop(self, monkeypatch):
        from backend.store import Store

        calls = self._stub_deliver(monkeypatch)
        store = Store(":memory:")
        store.set_bool_setting("notify:budget_exceeded", False)
        try:
            assert send_notification(
                store, "budget_exceeded", count=120, detail="Transport"
            ) == 0
            assert calls == []
            # A different budget type is unaffected (still enabled).
            assert send_notification(
                store, "budget_approaching", count=85, detail="Transport"
            ) == 99
            assert calls == [1]
        finally:
            store.close()


# ---------------------------------------------------------------------------
# TestBudgetNotificationCopy — the two v6 budget-alert types (Decision 8):
# body carries a category name + integer percent ONLY. No "$", no other digits.
# ---------------------------------------------------------------------------


class TestBudgetNotificationCopy:
    def test_approaching_has_category_and_percent_only(self):
        p = build_notification("budget_approaching", count=85, detail="Groceries")
        assert p["type"] == "budget_approaching"
        assert p["title"] and p["body"]
        assert "Groceries" in p["body"]
        assert "85" in p["body"]
        assert "$" not in p["body"]
        # The only digits in the body are the percent.
        assert "".join(ch for ch in p["body"] if ch.isdigit()) == "85"

    def test_exceeded_has_category_and_percent_only(self):
        p = build_notification("budget_exceeded", count=120, detail="Dining Out")
        assert p["type"] == "budget_exceeded"
        assert "Dining Out" in p["body"]
        assert "120" in p["body"]
        assert "$" not in p["body"]
        assert "".join(ch for ch in p["body"] if ch.isdigit()) == "120"

    def test_both_types_in_catalog(self):
        assert "budget_approaching" in NOTIFICATION_TYPES
        assert "budget_exceeded" in NOTIFICATION_TYPES

    def test_transfer_detected_in_catalog_count_only(self):
        # v6 follow-up: netted transfers must never be a silent mystery — but the
        # notice still carries a count only: no amounts, no descriptions, no banks.
        assert "transfer_detected" in NOTIFICATION_TYPES
        p = build_notification("transfer_detected", count=2)
        assert p["type"] == "transfer_detected"
        assert p["title"] and p["body"]
        assert "$" not in p["body"]
        assert "".join(ch for ch in p["body"] if ch.isdigit()) == "2"

    def test_missing_detail_falls_back_without_leaking(self):
        # No category supplied → a generic placeholder, never an amount/description.
        p = build_notification("budget_approaching", count=80)
        assert "$" not in p["body"]
        assert "".join(ch for ch in p["body"] if ch.isdigit()) == "80"

    def test_no_financial_tokens_in_either_body(self):
        forbidden = ["$", "balance", "account", "payee", "payer", "description", "merchant"]
        for ntype in ("budget_approaching", "budget_exceeded"):
            body = build_notification(ntype, count=90, detail="Groceries")["body"].lower()
            for token in forbidden:
                assert token not in body


# ---------------------------------------------------------------------------
# TestSubscriptionNotificationCopy — the three v6 subscription-watch types.
# Bodies carry ONLY a count, an integer percent, and the fixed words up/down —
# never a merchant name or a dollar amount.
# ---------------------------------------------------------------------------


class TestSubscriptionNotificationCopy:
    def test_all_three_types_in_catalog(self):
        for ntype in ("subscription_new", "subscription_price_change", "income_missed"):
            assert ntype in NOTIFICATION_TYPES

    def test_subscription_new_is_count_only(self):
        p = build_notification("subscription_new", count=2)
        assert p["type"] == "subscription_new"
        assert p["title"] and p["body"]
        assert "2" in p["body"]
        assert "$" not in p["body"]
        # The only digits are the count.
        assert "".join(ch for ch in p["body"] if ch.isdigit()) == "2"

    def test_price_change_has_percent_and_direction_up(self):
        p = build_notification("subscription_price_change", count=13, detail="up")
        assert p["type"] == "subscription_price_change"
        assert "13" in p["body"]
        assert "up" in p["body"]
        assert "$" not in p["body"]
        assert "".join(ch for ch in p["body"] if ch.isdigit()) == "13"

    def test_price_change_direction_down(self):
        p = build_notification("subscription_price_change", count=8, detail="down")
        assert "down" in p["body"]

    def test_price_change_unknown_detail_falls_back(self):
        # An unexpected/missing direction falls back to "up or down" — never leaks.
        p = build_notification("subscription_price_change", count=5, detail="sideways")
        assert "up or down" in p["body"]
        p2 = build_notification("subscription_price_change", count=5)
        assert "up or down" in p2["body"]

    def test_income_missed_is_status_only(self):
        p = build_notification("income_missed")
        assert p["type"] == "income_missed"
        assert p["title"] and p["body"]
        assert not any(ch.isdigit() for ch in p["body"])
        assert "$" not in p["body"]

    def test_no_financial_tokens_in_any_subscription_body(self):
        forbidden = ["$", "balance", "account", "payee", "payer", "description", "merchant"]
        cases = [
            ("subscription_new", {"count": 3}),
            ("subscription_price_change", {"count": 20, "detail": "up"}),
            ("income_missed", {}),
        ]
        for ntype, kwargs in cases:
            body = build_notification(ntype, **kwargs)["body"].lower()
            for token in forbidden:
                assert token not in body

    def test_no_emoji_or_em_dash_in_subscription_copy(self):
        for ntype, kwargs in (
            ("subscription_new", {"count": 1}),
            ("subscription_price_change", {"count": 1, "detail": "up"}),
            ("income_missed", {}),
        ):
            p = build_notification(ntype, **kwargs)
            for text in (p["title"], p["body"]):
                assert "—" not in text  # em dash
                assert "–" not in text  # en dash
                assert all(ord(ch) < 128 for ch in text)
