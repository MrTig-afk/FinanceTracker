"""notifier.py — Web Push "processed" notification send path (v2 Pass 3 — SCAFFOLD).

INERT BY DEFAULT. This module is a hard no-op unless a human deliberately activates
it (see "Activation" below). A bare ``import backend.notifier`` performs ZERO IO and
does NOT import ``pywebpush`` — that library is lazy-imported inside
``send_processed_notification`` and ONLY on the enabled path.

Fail-closed gate
-----------------
``send_processed_notification`` returns 0 with ZERO network calls whenever
``PUSH_ENABLED`` is not truthy, OR any VAPID_* value is missing/placeholder. This is
the default shipped state (PUSH_ENABLED unset, keys blank).

Privacy — HARD rule
--------------------
The push payload is a structured ``{"type", "title", "body"}`` object built by
``build_notification`` from the fixed catalog. Copy is COUNTS + STATUS ONLY, plus
(for budget + subscription types) a taxonomy category name and an integer percentage:
it may name a count ("N transactions"), a bank ("CommBank"/"Westpac"), a month
("YYYY-MM"), a taxonomy category name ("Groceries"), an integer percentage ("80%"), or
one of the fixed direction words "up"/"down" but NEVER dollar amounts, balances,
transaction descriptions, merchant/payee names, or account info. In particular the
subscription types (subscription_new / subscription_price_change / income_missed) are
limited to a count, an integer percent, and the fixed words "up"/"down" — never a
merchant name or an amount. No emojis, no em dashes. The service worker routes on the
``type`` field (in-app banner vs OS notification). Subscriptions are read from the
local Store only; this module never reads raw transaction data.

Activation (human-only; do NOT do this as part of the scaffold)
-----------------------------------------------------------------
1. Install the optional dependency:  ``pip install pywebpush``
   (deliberately NOT in requirements.txt while the feature is inert).
2. Generate a real VAPID key pair, e.g.:
     ``python -m py_vapid --gen``            (via the py-vapid package), or
     ``vapid --gen``                          (via the vapid CLI), or any standard
   EC P-256 VAPID key generator. NEVER commit the generated keys.
3. Paste the resulting public/private keys into your local ``.env`` (NOT
   ``.env.example``) as VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY, set VAPID_SUBJECT to
   a ``mailto:you@example.com`` address or your origin URL, and set
   ``PUSH_ENABLED=true``.
4. Also set the matching PUBLIC key as ``VITE_VAPID_PUBLIC_KEY`` in
   ``frontend/.env`` so the browser subscribes with the same key pair.
5. Restart the backend. From here, a real (non-no-op) pipeline run will call
   ``send_processed_notification`` and attempt a real, encrypted Web Push to each
   stored subscription — still carrying only the fixed generic content above.

Secrets
-------
All VAPID_* values and PUSH_ENABLED are read from .env via python-dotenv. Never
hardcoded here. This file's placeholders are non-secret sentinels only.
"""
from __future__ import annotations

import json
import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Notification content — HARD rule: COUNTS + STATUS ONLY.
# ---------------------------------------------------------------------------
# Every notification body carries only counts (N transactions) and status words
# (which bank, which month) — plus, for budget + subscription types, a taxonomy
# category name, an integer percentage, and (for a subscription price change) the fixed
# direction word "up"/"down" — NEVER dollar amounts, balances, merchant/payee names,
# transaction descriptions, account info, or dates beyond a "YYYY-MM" month tag. No
# emojis, no em dashes (plain hyphens only). The service worker routes on the structured
# `type` field (in-app banner vs OS notification); `title`/`body` are the human-visible
# copy.

NOTIFICATION_TITLE = "FinanceTracker"
# Legacy generic body retained for backward reference; the live catalog below
# supersedes it (send_processed_notification now emits a counted "processed" body).
NOTIFICATION_BODY = "Your statement was processed"

# The full notification catalog. Each entry is built into a structured payload
# {"type", "title", "body"} by build_notification().
NOTIFICATION_TYPES = (
    "processed",
    "processed_recovered",
    "categorisation_failed",
    "categorisation_recovered",
    "parse_error",
    "drive_backup_failed",
    "duplicate_noop",
    "generic_error",
    "monthly_reminder",
    "budget_approaching",
    "budget_exceeded",
    "transfer_detected",
    "subscription_new",
    "subscription_price_change",
    "income_missed",
)

_PLACEHOLDER_PUBLIC_KEY = "REPLACE_WITH_VAPID_PUBLIC_KEY"
_PLACEHOLDER_PRIVATE_KEY = "REPLACE_WITH_VAPID_PRIVATE_KEY"

_TRUTHY = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Payload catalog — builds the structured {type, title, body} the SW routes on.
# ---------------------------------------------------------------------------


def _count_str(count: int | None) -> str:
    """Render a safe count string. None/invalid -> 'some' (never leaks anything)."""
    if count is None:
        return "some"
    try:
        return str(int(count))
    except (TypeError, ValueError):
        return "some"


def build_notification(
    ntype: str, *, count: int | None = None, detail: str | None = None
) -> dict:
    """Build the structured push payload for one catalog type.

    Returns ``{"type": ntype, "title": str, "body": str}``. The body is COUNTS +
    STATUS ONLY (plus, for budget + subscription types, a taxonomy category name, an
    integer percentage, and the fixed direction word "up"/"down"): it may name a count,
    a bank ("CommBank"/"Westpac"), a month ("YYYY-MM"), a category name, an integer
    percentage, or "up"/"down" but NEVER dollar amounts, balances, merchants,
    transaction descriptions, or account info.

    Raises ValueError for an unknown type (fail loud in code; callers in the
    pipeline pass only known constants).
    """
    n = _count_str(count)

    if ntype == "processed":
        title = "Statements processed"
        body = f"Statements processed - {n} transactions sorted."
    elif ntype == "processed_recovered":
        title = "Backend back online"
        body = (
            f"Backend back online - your queued upload is now processed "
            f"({n} transactions)."
        )
    elif ntype == "categorisation_failed":
        title = "Sorting delayed"
        body = (
            f"Sorting is delayed - {n} transactions are saved and waiting to be "
            f"sorted. Open the app to retry."
        )
    elif ntype == "categorisation_recovered":
        title = "Sorting caught up"
        body = f"Sorting caught up - {n} transactions are now sorted."
    elif ntype == "parse_error":
        bank = detail or "a"
        title = "Could not read a statement"
        body = f"Could not read your {bank} statement - open the app to check."
    elif ntype == "drive_backup_failed":
        month = detail or "the latest month"
        title = "Backup failed"
        body = f"Backup to Drive failed for {month} - open the app to check."
    elif ntype == "duplicate_noop":
        title = "Nothing new"
        body = "Nothing new to process - this upload was already handled."
    elif ntype == "generic_error":
        title = "Something went wrong"
        body = "Something went wrong on the last run - open the app to check."
    elif ntype == "monthly_reminder":
        title = "New month"
        body = "New month - time to export and upload this month's statements."
    elif ntype == "budget_approaching":
        cat = detail or "A category"
        title = "Budget alert"
        body = f"{cat} is at {n}% of its monthly budget."
    elif ntype == "budget_exceeded":
        cat = detail or "A category"
        title = "Budget exceeded"
        body = f"{cat} has passed its monthly budget ({n}% spent)."
    elif ntype == "transfer_detected":
        title = "Transfers detected"
        body = (
            f"{n} transfer(s) between your own accounts were excluded from "
            f"spending - review them in the Transfers tab."
        )
    elif ntype == "subscription_new":
        title = "New subscription detected"
        body = f"Detected {n} new recurring payment(s) - open the app to see the details."
    elif ntype == "subscription_price_change":
        direction = detail if detail in ("up", "down") else "up or down"
        title = "Subscription price change"
        body = (
            f"A recurring payment's price went {direction} by about {n}% - "
            f"open the app for details."
        )
    elif ntype == "income_missed":
        title = "Expected deposit not seen"
        body = "A regular income deposit did not arrive last month - open the app to check."
    else:
        raise ValueError(f"unknown notification type: {ntype!r}")

    return {"type": ntype, "title": title, "body": body}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def push_config(
    *,
    enabled: bool | None = None,
    public_key: str | None = None,
    private_key: str | None = None,
    subject: str | None = None,
) -> dict:
    """Resolve push config from args > .env > safe defaults. No IO beyond load_dotenv().

    Returns {"enabled": bool, "public_key": str, "private_key": str, "subject": str}.
    PUSH_ENABLED parsed truthy only for {"1","true","yes","on"} (case-insensitive).
    """
    load_dotenv()  # no-op if already loaded; safe to call multiple times

    if enabled is None:
        enabled = os.getenv("PUSH_ENABLED", "false").strip().lower() in _TRUTHY

    if public_key is None:
        public_key = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    if private_key is None:
        private_key = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    if subject is None:
        subject = os.getenv("VAPID_SUBJECT", "").strip()

    return {
        "enabled": bool(enabled),
        "public_key": public_key,
        "private_key": private_key,
        "subject": subject,
    }


def is_push_enabled(cfg: dict | None = None) -> bool:
    """FAIL-CLOSED gate. True ONLY when ALL hold:
      - cfg['enabled'] is True, AND
      - public_key non-empty and != placeholder, AND
      - private_key non-empty and != placeholder, AND
      - subject non-empty.
    Any missing/placeholder value -> False (hard no-op).
    """
    if cfg is None:
        cfg = push_config()

    if not cfg.get("enabled"):
        return False
    public_key = cfg.get("public_key") or ""
    private_key = cfg.get("private_key") or ""
    subject = cfg.get("subject") or ""

    if not public_key or public_key == _PLACEHOLDER_PUBLIC_KEY:
        return False
    if not private_key or private_key == _PLACEHOLDER_PRIVATE_KEY:
        return False
    if not subject:
        return False
    return True


# ---------------------------------------------------------------------------
# Send path
# ---------------------------------------------------------------------------


def _deliver(store, payload: dict, config: dict | None) -> int:
    """Fail-closed core send: encrypt-and-push ``payload`` to every stored sub.

    HARD NO-OP (returns 0, ZERO network calls, does NOT import pywebpush) when
    is_push_enabled() is False -- i.e. by default (PUSH_ENABLED unset) or when any
    VAPID key is missing/placeholder. This is the fail-closed default shipped state.
    An empty subscription list is also a no-op (returns 0).

    When enabled + real-keyed (future activation): lazy import pywebpush, iterate
    store.list_push_subscriptions(), and send an ENCRYPTED Web Push whose data is
    ONLY the structured {type, title, body} ``payload`` -- counts/status only,
    NEVER any transaction data. Each send is wrapped so one failed endpoint never
    aborts the loop; failures are logged as safe counts only (never endpoint/key
    values). Returns the number of sends attempted.
    """
    if config is None:
        config = push_config()

    if not is_push_enabled(config):
        # Fail-closed default: no network, no pywebpush import.
        return 0

    # Lazy import — only reached on the enabled + real-keyed path.
    from pywebpush import webpush, WebPushException  # noqa: PLC0415

    subscriptions = store.list_push_subscriptions()
    if not subscriptions:
        return 0

    data = json.dumps(payload)
    vapid_claims = {"sub": config["subject"]}

    attempted = 0
    failures = 0
    for sub in subscriptions:
        attempted += 1
        try:
            webpush(
                subscription_info=sub,
                data=data,
                vapid_private_key=config["private_key"],
                vapid_claims=dict(vapid_claims),
            )
        except WebPushException:
            failures += 1
        except Exception:  # noqa: BLE001 — one bad endpoint must never abort the loop
            failures += 1

    if failures:
        logger.info(
            "push notification (%s): %d/%d sends failed",
            payload.get("type", "?"),
            failures,
            attempted,
        )

    return attempted


def send_notification(
    store,
    ntype: str,
    *,
    count: int | None = None,
    detail: str | None = None,
    config: dict | None = None,
) -> int:
    """Build the ``ntype`` catalog payload and best-effort push it to all subs.

    Per-type opt-out gate (Feature E): a HARD no-op (returns 0, no payload build,
    no delivery) when the owner has disabled this notification type
    (store.notification_enabled(ntype) is False). The default is enabled, so an
    owner who never touched settings still gets everything. A settings-read failure
    must never break a run, so any error reading the flag falls back to enabled (the
    safe default) rather than silently dropping the notification.

    Fail-closed: even when the type is enabled, delivery is still a hard no-op
    (returns 0) unless push is enabled with real VAPID keys AND at least one
    subscription exists. Never raises for a delivery/keying problem; only an unknown
    ``ntype`` raises ValueError (programmer error).
    """
    try:
        if store is not None and not store.notification_enabled(ntype):
            return 0
    except Exception:  # noqa: BLE001 — a settings read must never break delivery
        pass
    payload = build_notification(ntype, count=count, detail=detail)
    return _deliver(store, payload, config)


def send_processed_notification(
    store,
    *,
    count: int | None = None,
    recovered: bool = False,
    config: dict | None = None,
) -> int:
    """Best-effort 'processed' (or 'processed_recovered') push to all subs.

    ``recovered=True`` is used when a previously-queued upload is flushed after the
    backend was offline. Fail-closed no-op posture is unchanged (see _deliver).
    """
    ntype = "processed_recovered" if recovered else "processed"
    return send_notification(store, ntype, count=count, config=config)


def send_monthly_reminder(store, *, config: dict | None = None) -> int:
    """Best-effort 'new month, upload your statements' reminder to all subs.

    Called by the always-on service (Task Scheduler) via POST /notify/monthly-reminder.
    Fail-closed no-op posture is unchanged (see _deliver).
    """
    return send_notification(store, "monthly_reminder", config=config)
