"""Shared pytest fixtures for the backend suite.

Test hermeticity: the suite must not depend on the developer's local ``.env``.
In particular, once Web Push is activated locally (``PUSH_ENABLED=true`` with real
VAPID keys), the notifier is no longer a no-op — which would break every test that
asserts the fail-closed default. Neutralise those vars for every test by setting
them to empty; a test that exercises the enabled path overrides them explicitly.

Empty (not deleted): push_config() calls load_dotenv(), and load_dotenv does NOT
override an already-present env var (override=False), so an empty value survives
the .env read. A deleted key would be repopulated from .env instead.
"""
from __future__ import annotations

import pytest

_PUSH_ENV_KEYS = (
    "PUSH_ENABLED",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
    "VAPID_SUBJECT",
)


@pytest.fixture(autouse=True)
def _neutralise_push_env(monkeypatch):
    """Force the Web Push env vars to empty so the fail-closed default holds."""
    for key in _PUSH_ENV_KEYS:
        monkeypatch.setenv(key, "")
