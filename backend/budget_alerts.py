"""budget_alerts.py — per-category monthly budget threshold checks (v6 feature 3).

Evaluates the latest data month's per-category spend against owner-set monthly dollar
budgets and fires an at-most-once notification at 80% (approaching) and 100% (exceeded)
through the existing fail-closed notifier.

Privacy posture
---------------
100% LOCAL computation. This module operates only on local aggregates already produced
by the store (budgets from app_settings, spend totals from store.summary). It performs
NO IO at import and has ZERO network of its own — the only delivery path is the existing
fail-closed notifier (a hard no-op unless push is enabled with real VAPID keys and a
subscription exists). The ONLY strings that can reach the notifier for these types are a
taxonomy category NAME and an INTEGER percent — never dollar amounts, balances,
transaction descriptions, merchant names, or account info.
"""
from __future__ import annotations

from decimal import Decimal

from backend.notifier import send_notification
from backend.store.taxonomy import BUDGET_CATEGORIES

APPROACHING_THRESHOLD = 80   # percent
EXCEEDED_THRESHOLD = 100     # percent


def evaluate_budgets(
    budgets: dict[str, Decimal], totals: dict[str, str]
) -> list[tuple[str, int, int]]:
    """Pure evaluation of crossed thresholds -> [(category, threshold, percent)].

    For each budgeted category (intersection with BUDGET_CATEGORIES) with budget > 0:
      spend   = max(0, -Decimal(totals.get(category, "0")))   # net-credit -> 0
      percent = int((spend / budget) * 100)                   # Decimal, floor via int()
    Then:
      percent >= 100          -> [(cat, 80, percent), (cat, 100, percent)]
      80 <= percent < 100     -> [(cat, 80, percent)]
      percent < 80            -> (nothing)

    Both slots are claimed at >=100 so a later stray 'approaching' can never fire after
    an 'exceeded' (the caller only SENDS for the 100 slot in that case). No store, no
    network — fully unit-testable.
    """
    crossed: list[tuple[str, int, int]] = []
    for category, budget in budgets.items():
        if category not in BUDGET_CATEGORIES or budget <= 0:
            continue
        total = Decimal(totals.get(category, "0"))
        spend = -total if total < 0 else Decimal("0")
        percent = int((spend / budget) * 100)
        if percent >= EXCEEDED_THRESHOLD:
            crossed.append((category, APPROACHING_THRESHOLD, percent))
            crossed.append((category, EXCEEDED_THRESHOLD, percent))
        elif percent >= APPROACHING_THRESHOLD:
            crossed.append((category, APPROACHING_THRESHOLD, percent))
    return crossed


def check_budget_alerts(store, *, config=None) -> int:
    """Run the budget check for the latest data month; send at-most-once alerts.

    Steps:
      1. budgets = store.get_budgets(); return 0 if empty.
      2. ym = store.latest_year_month(); return 0 if None.
      3. totals = store.summary(ym)["totals"]  (Transfer rows already excluded).
      4. for (cat, threshold, percent) in evaluate_budgets(...):
           claim the (cat, ym, threshold) slot; only send on a fresh claim:
             threshold == 100 -> budget_exceeded
             percent < 100    -> budget_approaching
             (threshold 80 claimed while percent >= 100 -> claim only, NO send)
      5. Return the number of notifications sent.

    Entirely wrapped in try/except -> return 0: a budget-check failure must NEVER break
    an upload, an override, or a settings save (mirrors pipeline._notify). Delivery is
    the already fail-closed send_notification, which also honours the per-type
    notify:<ntype> toggle. `config` is forwarded to send_notification (tests inject a
    synthetic enabled config).
    """
    try:
        budgets = store.get_budgets()
        if not budgets:
            return 0

        ym = store.latest_year_month()
        if ym is None:
            return 0

        totals = store.summary(ym)["totals"]

        sent = 0
        for category, threshold, percent in evaluate_budgets(budgets, totals):
            if not store.claim_budget_alert(category, ym, threshold):
                continue  # already fired this month — never re-fire
            if threshold == EXCEEDED_THRESHOLD:
                sent += send_notification(
                    store, "budget_exceeded", count=percent, detail=category,
                    config=config,
                )
            elif percent < EXCEEDED_THRESHOLD:
                sent += send_notification(
                    store, "budget_approaching", count=percent, detail=category,
                    config=config,
                )
            # threshold == 80 while percent >= 100: slot claimed to suppress a later
            # stray 'approaching', but nothing is sent (the 100 slot carries the send).
        return sent
    except Exception:  # noqa: BLE001 — a budget check must never break the caller
        return 0
