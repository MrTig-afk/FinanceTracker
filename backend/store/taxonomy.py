"""taxonomy.py — fixed v1 category taxonomy for FinanceTracker (FR-25 / PRD §17).

TAXONOMY is the canonical, ordered tuple of valid category labels.
coerce_category normalises any label from an LLM response or user input to a valid
taxonomy member using a fail-soft strategy (unknown/empty/None -> 'Other').

No IO, no network, no secrets. Safe to import at module level.
"""
from __future__ import annotations

# Fixed v1 taxonomy — FR-25 / PRD §17. Order is the canonical display order.
TAXONOMY: tuple[str, ...] = (
    "Groceries",
    "Housing",
    "Dining Out",
    "Transport",
    "Entertainment",
    "Subscriptions",
    "Income",
    "Other",
)

OTHER = "Other"

_TAXONOMY_SET: frozenset[str] = frozenset(TAXONOMY)


def coerce_category(label: str | None) -> str:
    """Return label if it is a valid taxonomy member, else 'Other' (fail-soft, FR-25).

    Matching is exact on the canonical strings defined in TAXONOMY.
    None or empty string -> 'Other'.
    Any unrecognised string -> 'Other'.
    Never raises.
    """
    if label and label in _TAXONOMY_SET:
        return label
    return OTHER
