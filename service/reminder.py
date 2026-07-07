"""Monthly "export and upload your statements" reminder for the supervisor.

The backend already owns the notification itself (``POST /notify/monthly-reminder``
-> notifier.send_monthly_reminder); this module is only the scheduler side. The
supervisor calls ``run_reminder_if_due`` on every tick. Once per calendar month,
inside a sane waking-hours window, it POSTs to the backend on loopback and records
the month in a small state file under ``data/`` (gitignored, never leaves this
machine). The state is written ONLY after the backend confirms the send, so a
failed attempt (backend still booting, mid-restart) retries naturally on a later
tick with no log spam. This module makes no network call other than to
``127.0.0.1`` and carries no transaction data - the payload lives in the backend.
"""

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

STATE_FILENAME = "reminder-state.json"
REMINDER_PATH = "/notify/monthly-reminder"
REMIND_ON_DAY = 1
SEND_HOUR_MIN = 8
SEND_HOUR_MAX = 22
REQUEST_TIMEOUT_S = 10


def read_backend_port(repo: Path) -> int:
    """Read only BACKEND_PORT from ``repo/.env``; default 8010.

    Deliberately duplicates the tiny .env parse from supervisor.read_backend_bind
    (the same way backup.resolve_source_db duplicates the backend's SQLITE_PATH
    logic) so this module stays importable on its own.
    """
    port = 8010
    env_file = repo / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "BACKEND_PORT" and value.strip().isdigit():
                port = int(value.strip())
    return port


def state_path(repo: Path) -> Path:
    """Location of the last-sent state file (under gitignored ``data/``)."""
    return repo / "data" / STATE_FILENAME


def load_last_sent(path: Path) -> str | None:
    """Return the last-sent month as ``"YYYY-MM"``, or None if unknown.

    A missing, unreadable, or corrupt state file means "never sent" - the
    reminder fires again rather than silently going quiet forever.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("last_sent") if isinstance(data, dict) else None
    if isinstance(value, str) and len(value) == 7 and value[4] == "-":
        return value
    return None


def save_last_sent(path: Path, year_month: str) -> None:
    """Atomically record ``year_month`` as sent (tmp sibling + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"last_sent": year_month}), encoding="utf-8")
    os.replace(tmp, path)


def is_due(last_sent: str | None, now: dt.datetime) -> bool:
    """True when this month's reminder has not been sent and now is a sane time.

    Fires on or after REMIND_ON_DAY (``>=`` so a laptop that was off on the 1st
    still catches up later in the month) and only between SEND_HOUR_MIN and
    SEND_HOUR_MAX local time, so an awake-at-3am laptop stays quiet.
    """
    if now.day < REMIND_ON_DAY:
        return False
    if not (SEND_HOUR_MIN <= now.hour < SEND_HOUR_MAX):
        return False
    return now.strftime("%Y-%m") != last_sent


def post_notify(port: int, path: str, timeout: float = REQUEST_TIMEOUT_S) -> bool:
    """POST to the backend on loopback; True only for HTTP 200 + ``{"ok": true}``.

    Loopback only - this function never contacts any other host. All expected
    failure modes (backend down, timeout, non-200, junk body) return False.
    """
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return False
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return isinstance(body, dict) and body.get("ok") is True


def run_reminder_if_due(repo: Path, now: dt.datetime | None = None) -> str | None:
    """Send the monthly reminder if due; return a log line or None.

    Returns None on the common not-due path AND on a failed send (no log spam
    every 15s while the backend boots); a failed send writes no state, so the
    next tick retries. State is written only after the backend confirms.
    """
    now = now or dt.datetime.now()
    path = state_path(repo)
    if not is_due(load_last_sent(path), now):
        return None
    if not post_notify(read_backend_port(repo), REMINDER_PATH):
        return None
    year_month = now.strftime("%Y-%m")
    save_last_sent(path, year_month)
    return f"monthly reminder sent for {year_month}"
