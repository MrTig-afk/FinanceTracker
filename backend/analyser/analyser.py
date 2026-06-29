"""analyser.py — categorise() orchestration and build_prompt() (§7.6, FR-24, FR-25).

Receives ONLY the sanitised payload (SanitisedTxn tuples) from §7.5 sanitiser.
NEVER imports or touches raw Transaction data — the function signature enforces this.

Privacy invariants:
- build_prompt() constructs a user_prompt that is a JSON array of ONLY
  {row_index, cleaned_description, amount}.  SanitisedTxn has no other fields,
  so raw dates, banks, account numbers, and balances cannot leak structurally.
- Category totals are computed LOCALLY from sanitised amounts — we never trust
  or use any totals the LLM might include in its response.
- Empty payload → returns empty AnalysisResult with ZERO HTTP calls (FR-15).
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Sequence

from backend.sanitiser import SanitisedTxn, SanitiseResult
from backend.store import TAXONOMY, amount_to_text, coerce_category

from .client import OpenRouterClient
from .models import AnalysisResult


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompt(payload: Sequence[SanitisedTxn]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) built ONLY from sanitised tuples.

    system_prompt
        Static instruction string.  The category list is derived from the imported
        TAXONOMY tuple — one source of truth; the words are never hardcoded again.

    user_prompt
        A JSON array of objects, each containing exactly:
        ``{"row_index": int, "cleaned_description": str, "amount": str}``
        This is the entire set of data that travels off-machine.
    """
    taxonomy_labels = ", ".join(TAXONOMY)

    system_prompt = (
        "You are a financial transaction categoriser. "
        f"Categorise each transaction into exactly one of these categories: {taxonomy_labels}. "
        "Use the exact category spelling. "
        "Respond with JSON only — no prose, no markdown fences. "
        "Use this exact JSON schema:\n"
        "{\n"
        '  "categories": {"<row_index>": "<Category>", ...},\n'
        '  "summary": "<one or two sentence overview>",\n'
        '  "flagged": [<row_index>, ...]\n'
        "}\n"
        'The "flagged" list should contain row_index values for unusually large or '
        "atypical spends. "
        "Note: any category_totals field you include will be ignored; "
        "totals are always computed locally."
    )

    # user_prompt: exactly (row_index, cleaned_description, amount) — nothing else.
    # SanitisedTxn has no other fields, so raw data cannot leak structurally.
    transactions = [
        {
            "row_index": txn.row_index,
            "cleaned_description": txn.cleaned_description,
            "amount": amount_to_text(txn.amount),
        }
        for txn in payload
    ]
    user_prompt = json.dumps(transactions, ensure_ascii=False)

    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def categorise(
    result: SanitiseResult | Sequence[SanitisedTxn],
    *,
    client: OpenRouterClient | None = None,
) -> AnalysisResult:
    """Categorise a sanitised batch of transactions using the LLM via OpenRouter.

    Accepts either a SanitiseResult (from the sanitiser stage) or a plain sequence
    of SanitisedTxn objects.  NEVER accepts raw Transaction data.

    Empty payload → returns empty AnalysisResult with ZERO HTTP calls (FR-15).
    Category totals are computed LOCALLY; LLM-supplied totals are ignored.
    Dropped rows (present in SanitiseResult.dropped) are labelled "Other" locally
    without an LLM call and excluded from category_totals.

    Raises AnalyserError if the client exhausts all model tiers.
    """
    # ------------------------------------------------------------------
    # Step 1: Normalise input
    # ------------------------------------------------------------------
    if isinstance(result, SanitiseResult):
        payload: tuple[SanitisedTxn, ...] = result.payload
        dropped: tuple[int, ...] = result.dropped
    else:
        payload = tuple(result)
        dropped = ()

    # ------------------------------------------------------------------
    # Step 2: Empty-payload short-circuit (FR-15) — ZERO HTTP calls
    # ------------------------------------------------------------------
    if not payload:
        return AnalysisResult(
            categories={i: "Other" for i in dropped},
            category_totals={},
            summary="",
            flagged=[],
            model_used="",
        )

    # ------------------------------------------------------------------
    # Step 3: Lazy-construct client (only when we actually need to call)
    # ------------------------------------------------------------------
    if client is None:
        client = OpenRouterClient()

    # ------------------------------------------------------------------
    # Step 4: Build prompt from sanitised tuples only
    # ------------------------------------------------------------------
    system_prompt, user_prompt = build_prompt(payload)

    # ------------------------------------------------------------------
    # Step 5: Call the LLM — AnalyserError propagates to the caller
    # ------------------------------------------------------------------
    parsed, model_used = client.complete(
        system_prompt=system_prompt, user_prompt=user_prompt
    )

    # ------------------------------------------------------------------
    # Step 6: Map categories defensively (never crash on one bad field)
    # ------------------------------------------------------------------
    raw_cats = parsed.get("categories", {})
    if not isinstance(raw_cats, dict):
        raw_cats = {}

    payload_index_set: set[int] = {txn.row_index for txn in payload}
    categories: dict[int, str] = {}

    for txn in payload:
        # Look up by str(row_index) first (JSON keys are always strings),
        # then fall back to int key in case the model returned numeric keys.
        raw_value = raw_cats.get(str(txn.row_index), raw_cats.get(txn.row_index))
        label_str = (raw_value or "").strip() if raw_value is not None else ""
        categories[txn.row_index] = coerce_category(label_str)

    # Dropped rows are labelled "Other" locally — no amount, excluded from totals
    for i in dropped:
        categories[i] = "Other"

    # ------------------------------------------------------------------
    # Step 7: Compute category_totals LOCALLY — never trust LLM math
    # ------------------------------------------------------------------
    totals: dict[str, Decimal] = {}
    for txn in payload:
        cat = categories[txn.row_index]
        totals[cat] = totals.get(cat, Decimal("0")) + txn.amount

    category_totals: dict[str, str] = {
        cat: amount_to_text(amt) for cat, amt in totals.items()
    }

    # ------------------------------------------------------------------
    # Step 8: summary
    # ------------------------------------------------------------------
    summary: str = str(parsed.get("summary", "") or "")

    # ------------------------------------------------------------------
    # Step 9: flagged — keep only valid ints present in payload index set
    # ------------------------------------------------------------------
    raw_flagged = parsed.get("flagged", [])
    flagged: list[int] = []
    if isinstance(raw_flagged, list):
        for entry in raw_flagged:
            try:
                idx = int(entry)
            except (TypeError, ValueError):
                continue
            if idx in payload_index_set:
                flagged.append(idx)

    # ------------------------------------------------------------------
    # Step 10: Return result
    # ------------------------------------------------------------------
    return AnalysisResult(
        categories=categories,
        category_totals=category_totals,
        summary=summary,
        flagged=flagged,
        model_used=model_used,
    )
