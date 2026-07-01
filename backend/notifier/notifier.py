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
The push payload is ALWAYS the fixed generic pair
``{"title": NOTIFICATION_TITLE, "body": NOTIFICATION_BODY}``. It NEVER carries
amounts, balances, descriptions, categories, counts, account info, or dates.
Subscriptions are read from the local Store only; this module never reads raw
transaction data.

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
# Fixed generic notification content — HARD rule, no financial data.
# ---------------------------------------------------------------------------

NOTIFICATION_TITLE = "FinanceTracker"
NOTIFICATION_BODY = "Your statement was processed"

_PLACEHOLDER_PUBLIC_KEY = "REPLACE_WITH_VAPID_PUBLIC_KEY"
_PLACEHOLDER_PRIVATE_KEY = "REPLACE_WITH_VAPID_PRIVATE_KEY"

_TRUTHY = {"1", "true", "yes", "on"}


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


def send_processed_notification(store, *, config: dict | None = None) -> int:
    """Best-effort generic 'processed' push to all stored subscriptions.

    HARD NO-OP (returns 0, ZERO network calls, does NOT import pywebpush) when
    is_push_enabled() is False -- i.e. by default (PUSH_ENABLED unset) or when any
    VAPID key is missing/placeholder. This is the fail-closed default shipped state.

    When enabled + real-keyed (future activation): lazy `from pywebpush import webpush,
    WebPushException`, iterate store.list_push_subscriptions(), and for each send an
    ENCRYPTED Web Push whose payload is ONLY the fixed generic
    {"title": NOTIFICATION_TITLE, "body": NOTIFICATION_BODY} -- NEVER any transaction data.
    vapid_private_key + vapid_claims{'sub': subject} from config. Each send wrapped in
    try/except so one failed endpoint never aborts the loop; failures are logged as safe
    counts only (never endpoint/key values). Returns the number of sends attempted.
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

    payload = json.dumps({"title": NOTIFICATION_TITLE, "body": NOTIFICATION_BODY})
    vapid_claims = {"sub": config["subject"]}

    attempted = 0
    failures = 0
    for sub in subscriptions:
        attempted += 1
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=config["private_key"],
                vapid_claims=dict(vapid_claims),
            )
        except WebPushException:
            failures += 1
        except Exception:  # noqa: BLE001 — one bad endpoint must never abort the loop
            failures += 1

    if failures:
        logger.info("push notification: %d/%d sends failed", failures, attempted)

    return attempted
