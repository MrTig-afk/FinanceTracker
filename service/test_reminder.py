"""test_reminder.py — pytest suite for the supervisor monthly-reminder module.

ALL fixtures use synthetic data generated inline; no real transactions anywhere.
Every path is under tmp_path — NEVER the real ./data/ or .env.
No network calls anywhere in this file: urllib.request.urlopen is always
monkeypatched. The supervisor loop is never started; only service.reminder is
imported, never service.supervisor.main.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.error
from pathlib import Path

from service import reminder

# ---------------------------------------------------------------------------
# Synthetic helpers
# ---------------------------------------------------------------------------

# A datetime squarely inside the send window on a mid-month day.
_DUE_NOW = dt.datetime(2026, 7, 15, 10, 0)


class FakeResponse:
    """Minimal stand-in for the urlopen context-manager response."""

    def __init__(self, status: int = 200, body: bytes = b'{"ok": true, "sent": 1}'):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def make_repo(tmp_path: Path, port: int | None = None) -> Path:
    """Create a synthetic repo root, optionally with a .env naming a port."""
    repo = tmp_path / "repo"
    repo.mkdir()
    if port is not None:
        (repo / ".env").write_text(f"BACKEND_PORT={port}\n", encoding="utf-8")
    return repo


def patch_urlopen(monkeypatch, response=None, exc: Exception | None = None):
    """Patch urlopen to capture requests and return ``response`` or raise ``exc``."""
    calls: list = []

    def fake_urlopen(request, timeout=None):
        calls.append((request, timeout))
        if exc is not None:
            raise exc
        return response if response is not None else FakeResponse()

    monkeypatch.setattr(reminder.urllib.request, "urlopen", fake_urlopen)
    return calls


# ---------------------------------------------------------------------------
# read_backend_port
# ---------------------------------------------------------------------------


class TestReadBackendPort:
    def test_defaults_to_8010_without_env(self, tmp_path):
        assert reminder.read_backend_port(make_repo(tmp_path)) == 8010

    def test_reads_port_from_env(self, tmp_path):
        assert reminder.read_backend_port(make_repo(tmp_path, port=9000)) == 9000

    def test_ignores_non_digit_values(self, tmp_path):
        repo = make_repo(tmp_path)
        (repo / ".env").write_text(
            "BACKEND_PORT=nope\nOTHER_KEY=5\n", encoding="utf-8"
        )
        assert reminder.read_backend_port(repo) == 8010


# ---------------------------------------------------------------------------
# is_due
# ---------------------------------------------------------------------------


class TestIsDue:
    def test_never_sent_first_of_month_is_due(self):
        assert reminder.is_due(None, dt.datetime(2026, 7, 1, 10, 0)) is True

    def test_same_month_already_sent_not_due(self):
        assert reminder.is_due("2026-07", _DUE_NOW) is False

    def test_next_month_due_again(self):
        assert reminder.is_due("2026-06", _DUE_NOW) is True

    def test_catch_up_mid_month_when_laptop_was_off(self):
        # Laptop off on the 1st: day check is >=, so the 15th still fires.
        assert reminder.is_due(None, _DUE_NOW) is True

    def test_before_window_not_due(self):
        assert reminder.is_due(None, dt.datetime(2026, 7, 15, 7, 59)) is False

    def test_at_window_start_is_due(self):
        assert reminder.is_due(None, dt.datetime(2026, 7, 15, 8, 0)) is True

    def test_at_window_end_not_due(self):
        assert reminder.is_due(None, dt.datetime(2026, 7, 15, 22, 0)) is False


# ---------------------------------------------------------------------------
# state file
# ---------------------------------------------------------------------------


class TestState:
    def test_missing_file_means_never_sent(self, tmp_path):
        assert reminder.load_last_sent(tmp_path / "absent.json") is None

    def test_corrupt_json_means_never_sent(self, tmp_path):
        path = tmp_path / "reminder-state.json"
        path.write_text("{not json", encoding="utf-8")
        assert reminder.load_last_sent(path) is None

    def test_wrong_shape_means_never_sent(self, tmp_path):
        path = tmp_path / "reminder-state.json"
        path.write_text(json.dumps({"last_sent": 202607}), encoding="utf-8")
        assert reminder.load_last_sent(path) is None

    def test_save_round_trips_and_is_atomic(self, tmp_path):
        path = tmp_path / "data" / "reminder-state.json"
        reminder.save_last_sent(path, "2026-07")
        assert reminder.load_last_sent(path) == "2026-07"
        assert not path.with_name(path.name + ".tmp").exists()


# ---------------------------------------------------------------------------
# post_notify
# ---------------------------------------------------------------------------


class TestPostNotify:
    def test_ok_true_returns_true_and_hits_loopback_only(self, monkeypatch):
        calls = patch_urlopen(monkeypatch)
        assert reminder.post_notify(8010, reminder.REMINDER_PATH) is True
        assert len(calls) == 1
        request, timeout = calls[0]
        assert request.full_url == "http://127.0.0.1:8010/notify/monthly-reminder"
        assert request.get_method() == "POST"
        assert timeout == reminder.REQUEST_TIMEOUT_S

    def test_ok_false_returns_false(self, monkeypatch):
        patch_urlopen(monkeypatch, response=FakeResponse(body=b'{"ok": false}'))
        assert reminder.post_notify(8010, reminder.REMINDER_PATH) is False

    def test_non_200_returns_false(self, monkeypatch):
        patch_urlopen(monkeypatch, response=FakeResponse(status=503))
        assert reminder.post_notify(8010, reminder.REMINDER_PATH) is False

    def test_junk_body_returns_false(self, monkeypatch):
        patch_urlopen(monkeypatch, response=FakeResponse(body=b"not json"))
        assert reminder.post_notify(8010, reminder.REMINDER_PATH) is False

    def test_url_error_returns_false(self, monkeypatch):
        patch_urlopen(monkeypatch, exc=urllib.error.URLError("refused"))
        assert reminder.post_notify(8010, reminder.REMINDER_PATH) is False


# ---------------------------------------------------------------------------
# run_reminder_if_due
# ---------------------------------------------------------------------------


class TestRunReminderIfDue:
    def test_due_and_ok_writes_state_and_returns_message(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path, port=9000)
        calls = patch_urlopen(monkeypatch)
        message = reminder.run_reminder_if_due(repo, now=_DUE_NOW)
        assert message == "monthly reminder sent for 2026-07"
        assert reminder.load_last_sent(reminder.state_path(repo)) == "2026-07"
        assert calls[0][0].full_url.startswith("http://127.0.0.1:9000/")

    def test_second_run_same_month_makes_no_network_call(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        patch_urlopen(monkeypatch)
        assert reminder.run_reminder_if_due(repo, now=_DUE_NOW) is not None

        def explode(*args, **kwargs):  # pragma: no cover - failure is the assert
            raise AssertionError("urlopen called on a not-due tick")

        monkeypatch.setattr(reminder.urllib.request, "urlopen", explode)
        assert reminder.run_reminder_if_due(repo, now=_DUE_NOW) is None

    def test_failed_send_writes_no_state_and_retries(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        patch_urlopen(monkeypatch, exc=urllib.error.URLError("refused"))
        assert reminder.run_reminder_if_due(repo, now=_DUE_NOW) is None
        assert reminder.load_last_sent(reminder.state_path(repo)) is None
        # Backend comes back: the same tick logic now succeeds.
        patch_urlopen(monkeypatch)
        assert reminder.run_reminder_if_due(repo, now=_DUE_NOW) is not None

    def test_out_of_window_makes_no_network_call(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)

        def explode(*args, **kwargs):  # pragma: no cover - failure is the assert
            raise AssertionError("urlopen called outside the send window")

        monkeypatch.setattr(reminder.urllib.request, "urlopen", explode)
        night = dt.datetime(2026, 7, 15, 3, 0)
        assert reminder.run_reminder_if_due(repo, now=night) is None
