"""transfer_rule.py — deterministic pairing of internal account-to-account transfers.

When the owner moves money between their OWN CommBank and Westpac accounts, the same
sum leaves one account (a debit) and arrives in the other (a credit) within a few days.
Both legs are real transactions, but neither is spending — counting them would double
the money's apparent movement and distort every category total. This module matches the
two legs so the store can tag them 'Transfer' and exclude them from spending aggregates.

The pairing is a pure function over local data: given the candidate rows, it returns the
(debit_id, credit_id) pairs. No IO, no network, no secrets. Like splitwise_rule.py it
operates only on the owner's own local data, which never leaves the machine — this
feature adds ZERO off-machine calls (it actually shrinks the LLM payload, since a tagged
row is no longer uncategorised).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

# Reserved system label written on both legs of a matched transfer. NOT a taxonomy
# member: it never enters TAXONOMY, the LLM prompt, the donut palette, or the drawer
# picker. Only the detection/untag store methods write it, via a direct UPDATE (see
# store.detect_transfers). Spending aggregates filter it out.
TRANSFER_CATEGORY: str = "Transfer"

# Inclusive calendar-day window between the two legs. Either side may post first, so the
# comparison is on the absolute day difference (interbank settlement lag, 0..3 days).
MAX_WINDOW_DAYS: int = 3


@dataclass(frozen=True)
class CandidateRow:
    """One transaction considered for transfer pairing.

    amount is the signed, exact stored value (debit negative, credit positive). bank is
    the Bank.value string ('commbank' | 'westpac'); a transfer must cross banks.
    """

    id: int
    date: str        # ISO 'YYYY-MM-DD'
    amount: Decimal  # signed, exact
    bank: str        # 'commbank' | 'westpac'


def pair_transfers(
    rows: Sequence[CandidateRow], *, max_days: int = MAX_WINDOW_DAYS
) -> list[tuple[int, int]]:
    """Deterministic one-to-one (debit_id, credit_id) transfer pairs. Never raises.

    Algorithm:
      1. Partition rows into debits (amount < 0) and credits (amount > 0); zero amounts
         are dropped (a signed pair needs a genuine debit and credit).
      2. Build candidate edges: every (debit, credit) that crosses banks
         (debit.bank != credit.bank), has exactly opposite magnitudes
         (credit.amount == -debit.amount, compared as exact Decimal), and whose legs
         post within `max_days` calendar days of each other (absolute difference).
      3. Sort edges by (day_distance ASC, debit.id ASC, credit.id ASC) so the closest
         dates win and ties break on the lowest ids.
      4. Greedy scan: accept an edge only when neither leg is already used, marking both
         used. A row therefore never appears in two pairs.

    The result is independent of input row order (the sort makes it deterministic).
    """
    debits = [r for r in rows if r.amount < 0]
    credits = [r for r in rows if r.amount > 0]

    edges: list[tuple[int, int, int]] = []  # (day_distance, debit_id, credit_id)
    for d in debits:
        d_date = date.fromisoformat(d.date)
        for c in credits:
            if d.bank == c.bank:
                continue
            if c.amount != -d.amount:
                continue
            distance = abs((date.fromisoformat(c.date) - d_date).days)
            if distance > max_days:
                continue
            edges.append((distance, d.id, c.id))

    edges.sort()

    used: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _distance, debit_id, credit_id in edges:
        if debit_id in used or credit_id in used:
            continue
        used.add(debit_id)
        used.add(credit_id)
        pairs.append((debit_id, credit_id))

    return pairs
