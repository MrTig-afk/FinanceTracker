"""subscriptions.py — recurring-merchant (subscription) detection (v6 feature 4).

Deterministic, 100% LOCAL detection of recurring payments (subscriptions) and regular
income deposits over the store's own transaction rows. NO LLM, NO network of its own:
the only delivery path is the existing fail-closed notifier. Three catalog notifications
are produced (new subscription / price change / missed income deposit), each carrying
ONLY a count, an integer percent, and a fixed direction word.

Privacy posture
---------------
100% LOCAL computation over local SQLite rows. Detection reuses the sanitiser's
scrubbers (scrub_description + the fail-closed residual gate) when normalising a
merchant root, so even the local `subscriptions` state table never stores account
numbers, names, phones, or references (defence in depth); an un-scrubbable row is
dropped from detection entirely. The ONLY strings that can reach the notifier for these
types are a COUNT, an INTEGER percent, and the fixed words "up"/"down" — never merchant
names, dollar amounts, balances, descriptions, or account info. This module performs
ZERO IO at import and has ZERO network of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.notifier import send_notification
from backend.sanitiser.scrub import has_residual_identifier, scrub_description
from backend.store.store import amount_from_text

MIN_STREAK_MONTHS = 3            # >= 3 consecutive months to detect a subscription
TOLERANCE_ABS = Decimal("1.00")  # within max($1.00, 5%) is "same amount"
TOLERANCE_PCT = Decimal("0.05")
END_GAP_MONTHS = 2               # absent 2+ months -> status 'ended'
EVENT_NEW = "new"
EVENT_PRICE = "price_change"
EVENT_MISSED = "missed_income"


# ---------------------------------------------------------------------------
# Normalisation — reuses the sanitiser scrubbers + fail-closed residual gate
# ---------------------------------------------------------------------------

def normalise_root(raw: str) -> str | None:
    """Scrub a raw description down to a stable, uppercased merchant root.

    Returns None (fail closed -> the row is excluded from detection entirely) when the
    scrubbed string still carries a residual identifier, which automatically drops
    P2P/PayID rows whose descriptions are mostly a person's name (they scrub to
    empty/residue). scrub_description already collapses whitespace and strips.
    """
    cleaned = scrub_description(raw)
    if has_residual_identifier(cleaned):
        return None
    return cleaned.upper()


# ---------------------------------------------------------------------------
# Month-string arithmetic (pure; no dateutil)
# ---------------------------------------------------------------------------

def _next_month(ym: str) -> str:
    """Return the calendar month immediately after 'YYYY-MM'."""
    year = int(ym[:4])
    month = int(ym[5:7]) + 1
    if month == 13:
        month = 1
        year += 1
    return f"{year:04d}-{month:02d}"


def _months_between(a: str, b: str) -> int:
    """Signed count of calendar months from 'YYYY-MM' a to b (b - a)."""
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    return (yb - ya) * 12 + (mb - ma)


# ---------------------------------------------------------------------------
# Grouping + amount stability + streak segmentation (pure functions)
# ---------------------------------------------------------------------------

def build_groups(rows) -> dict[tuple[str, str], dict[str, Decimal]]:
    """Group detection rows into {(direction, root): {qualifying_ym: abs_amount}}.

    Input rows (from store.subscription_detection_rows()) already exclude Transfer legs.
    For each row with amount != 0:
      - direction: 'spend' when amount < 0; 'income' when amount > 0 AND category ==
        'Income'. A credit not categorised Income is ignored (refunds are not subs).
      - root = normalise_root(description); None -> the row is dropped.
    A month QUALIFIES for a group iff the group has EXACTLY ONE row in that month
    (monthly cadence only — a multi-hit month like groceries never qualifies). The
    result maps each qualifying 'YYYY-MM' to the exact abs(amount) Decimal.
    """
    raw_groups: dict[tuple[str, str], dict[str, list[Decimal]]] = {}
    for row in rows:
        amount = amount_from_text(row["amount"])
        if amount == 0:
            continue
        if amount < 0:
            direction = "spend"
        elif row["category"] == "Income":
            direction = "income"
        else:
            continue  # credit not tagged Income -> not a subscription
        root = normalise_root(row["description"])
        if root is None:
            continue
        key = (direction, root)
        raw_groups.setdefault(key, {}).setdefault(row["year_month"], []).append(
            abs(amount)
        )

    groups: dict[tuple[str, str], dict[str, Decimal]] = {}
    for key, by_month in raw_groups.items():
        qualifying = {
            ym: amounts[0] for ym, amounts in by_month.items() if len(amounts) == 1
        }
        if qualifying:
            groups[key] = qualifying
    return groups


def amounts_close(a: Decimal, b: Decimal) -> bool:
    """True when |a - b| <= max(TOLERANCE_ABS, TOLERANCE_PCT * b).

    `b` is the reference (previous/expected) magnitude; both are absolute values.
    """
    return abs(a - b) <= max(TOLERANCE_ABS, TOLERANCE_PCT * b)


@dataclass(frozen=True)
class Segment:
    """One maximal consecutive-month streak within a group's qualifying months.

    `last_amount` is the abs magnitude of the streak's final (latest) month; `length`
    is the streak's month count.
    """

    start: str          # 'YYYY-MM'
    end: str            # 'YYYY-MM'
    last_amount: Decimal

    @property
    def length(self) -> int:
        return _months_between(self.start, self.end) + 1


def detect_segments(month_amounts: dict[str, Decimal]) -> list[Segment]:
    """Split qualifying months into maximal consecutive, in-tolerance streaks.

    Months are sorted chronologically. An adjacent pair (m_prev, m) stays in one
    segment iff m is the calendar month immediately after m_prev AND
    amounts_close(amount[m], amount[m_prev]) (tolerance vs the previous month, so
    gradual drift stays in one segment). A group is a subscription when it has a
    segment of length >= MIN_STREAK_MONTHS.
    """
    months = sorted(month_amounts)
    if not months:
        return []

    segments: list[Segment] = []
    seg_start = months[0]
    prev = months[0]
    prev_amount = month_amounts[prev]

    for m in months[1:]:
        amount = month_amounts[m]
        if m == _next_month(prev) and amounts_close(amount, prev_amount):
            prev = m
            prev_amount = amount
            continue
        segments.append(Segment(seg_start, prev, prev_amount))
        seg_start = m
        prev = m
        prev_amount = amount

    segments.append(Segment(seg_start, prev, prev_amount))
    return segments


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def check_subscriptions(store, *, config=None) -> int:
    """Detect subscriptions for the latest data month; send at-most-once notifications.

    Entirely wrapped in try/except -> return 0: a subscription check must NEVER break
    an upload, an override, or a settings save (mirrors check_budget_alerts). Delivery
    is the already fail-closed send_notification, which also honours the per-type
    notify:<ntype> toggle. `config` is forwarded to send_notification (tests inject a
    synthetic enabled config). Returns the number of notifications sent.

    Bootstrap (the first-ever run over an existing history — empty subscriptions table)
    populates state and CLAIMS event slots but SENDS NOTHING, so deploying this feature
    onto months of history never bursts.
    """
    try:
        latest = store.latest_year_month()
        rows = store.subscription_detection_rows()
        if latest is None or not rows:
            return 0

        bootstrap = not store.has_any_subscriptions()
        groups = build_groups(rows)
        existing = {s["merchant_key"]: s for s in store.get_subscriptions()}

        sent = 0
        new_count = 0

        for key in sorted(groups):
            direction, root = key
            merchant_key = f"{direction}:{root}"
            month_amounts = groups[key]

            if merchant_key not in existing:
                new_count += _handle_unknown_group(
                    store, merchant_key, direction, root, month_amounts, latest,
                    bootstrap,
                )
                continue

            sub = existing[merchant_key]
            if latest in month_amounts:
                sent += _handle_known_present(
                    store, sub, merchant_key, direction, root,
                    month_amounts[latest], latest, bootstrap, config,
                )
            else:
                sent += _handle_known_absent(
                    store, sub, merchant_key, direction, root, latest, bootstrap,
                    config,
                )

        if new_count > 0:
            sent += send_notification(
                store, "subscription_new", count=new_count, config=config,
            )
        return sent
    except Exception:  # noqa: BLE001 — a subscription check must never break the caller
        return 0


def _handle_unknown_group(
    store, merchant_key: str, direction: str, root: str,
    month_amounts: dict[str, Decimal], latest: str, bootstrap: bool,
) -> int:
    """New (no state row) group: create state from the latest qualifying streak.

    Returns 1 when a fresh 'new' slot was claimed for a non-bootstrap run (feeds the
    aggregated subscription_new count), else 0.
    """
    segments = [s for s in detect_segments(month_amounts) if s.length >= MIN_STREAK_MONTHS]
    if not segments:
        return 0
    seg = segments[-1]  # LATEST qualifying segment
    status = "ended" if _months_between(seg.end, latest) >= END_GAP_MONTHS else "active"
    store.upsert_subscription(
        merchant_key=merchant_key,
        root=root,
        direction=direction,
        expected_amount=seg.last_amount,
        first_seen_month=seg.start,
        last_seen_month=seg.end,
        status=status,
    )
    if status == "active" and seg.end == latest:
        if store.claim_subscription_event(merchant_key, latest, EVENT_NEW) and not bootstrap:
            return 1
    return 0


def _handle_known_present(
    store, sub: dict, merchant_key: str, direction: str, root: str,
    amount: Decimal, latest: str, bootstrap: bool, config,
) -> int:
    """Known subscription that qualifies in the latest month. Returns sends made."""
    expected = amount_from_text(sub["expected_amount"])
    first_seen = sub["first_seen_month"]

    if sub["status"] == "ended":
        # Quiet reactivation: back to active, no notification (and no price alert).
        store.upsert_subscription(
            merchant_key=merchant_key, root=root, direction=direction,
            expected_amount=amount, first_seen_month=first_seen,
            last_seen_month=latest, status="active",
        )
        return 0

    if amounts_close(amount, expected):
        # In tolerance: expected tracks the newest in-tolerance amount.
        store.upsert_subscription(
            merchant_key=merchant_key, root=root, direction=direction,
            expected_amount=amount, first_seen_month=first_seen,
            last_seen_month=latest, status="active",
        )
        return 0

    # Out of tolerance -> price change.
    sent = 0
    if store.claim_subscription_event(merchant_key, latest, EVENT_PRICE) and not bootstrap:
        percent = int(abs(amount - expected) / expected * 100)
        direction_word = "up" if amount > expected else "down"
        sent += send_notification(
            store, "subscription_price_change", count=percent, detail=direction_word,
            config=config,
        )
    store.upsert_subscription(
        merchant_key=merchant_key, root=root, direction=direction,
        expected_amount=amount, first_seen_month=first_seen,
        last_seen_month=latest, status="active",
    )
    return sent


def _handle_known_absent(
    store, sub: dict, merchant_key: str, direction: str, root: str,
    latest: str, bootstrap: bool, config,
) -> int:
    """Known subscription absent (or disqualified) in the latest month. Returns sends."""
    status = sub["status"]
    last_seen = sub["last_seen_month"]
    sent = 0

    # Missed income first: only for an active income sub, and only when data exists
    # for a month strictly after the first missed month M (not a mid-month export).
    if direction == "income" and status == "active":
        missed = _next_month(last_seen)
        if missed < latest and store.claim_subscription_event(
            merchant_key, missed, EVENT_MISSED
        ) and not bootstrap:
            sent += send_notification(store, "income_missed", config=config)

    # Then ended: 2+ month absence flips status to 'ended' quietly (no notification).
    if status == "active" and _months_between(last_seen, latest) >= END_GAP_MONTHS:
        store.upsert_subscription(
            merchant_key=merchant_key, root=root, direction=direction,
            expected_amount=amount_from_text(sub["expected_amount"]),
            first_seen_month=sub["first_seen_month"],
            last_seen_month=last_seen, status="ended",
        )
    return sent
