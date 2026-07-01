"""notifier — Web Push "processed" notification stage for FinanceTracker (v2 Pass 3).

CONFIG-GATED + FEATURE-FLAGGED OFF by default. A bare ``import backend.notifier``
does ZERO IO/network and does NOT import ``pywebpush``. See notifier.py for the
fail-closed gate (is_push_enabled) and the activation notes.
"""
from __future__ import annotations

from .notifier import is_push_enabled, push_config, send_processed_notification

__all__ = ["send_processed_notification", "is_push_enabled", "push_config"]
