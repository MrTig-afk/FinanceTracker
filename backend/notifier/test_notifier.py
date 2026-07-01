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

from backend.notifier import is_push_enabled, push_config, send_processed_notification
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
    """Ensure no real .env values bleed into these tests."""
    for key in ("PUSH_ENABLED", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        monkeypatch.delenv(key, raising=False)


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

    def test_payload_equals_fixed_generic_dict(self, monkeypatch):
        import json

        captured = []

        def _fake_webpush(*, data, **kwargs):
            captured.append(data)

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1])
        cfg = _enabled_config()
        send_processed_notification(store, config=cfg)

        assert len(captured) == 1
        payload = json.loads(captured[0])
        assert payload == {"title": "FinanceTracker", "body": "Your statement was processed"}

    def test_payload_contains_no_digits(self, monkeypatch):
        captured = []

        def _fake_webpush(*, data, **kwargs):
            captured.append(data)

        _install_fake_pywebpush(monkeypatch, webpush_fn=_fake_webpush)
        store = FakeStore([_SYNTH_SUB_1, _SYNTH_SUB_2])
        cfg = _enabled_config()
        send_processed_notification(store, config=cfg)

        for payload_str in captured:
            assert not any(ch.isdigit() for ch in payload_str), (
                f"payload must contain no digits (amounts/counts): {payload_str!r}"
            )

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
        for key in ("PUSH_ENABLED", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
            monkeypatch.delenv(key, raising=False)
        assert is_push_enabled() is False


# ---------------------------------------------------------------------------
# TestPushConfig — env resolution
# ---------------------------------------------------------------------------


class TestPushConfig:
    def test_defaults_when_env_unset(self, monkeypatch):
        for key in ("PUSH_ENABLED", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
            monkeypatch.delenv(key, raising=False)
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
