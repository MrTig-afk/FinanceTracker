"""models.py — result data structures for the §7.6 analyser stage (FR-22..FR-26).

AnalyserError    : raised when all 3 model tiers fail or the response is unusable.
Categorisation   : row_index + coerced TAXONOMY category (frozen dataclass).
AnalysisResult   : immutable batch result returned by categorise().

No IO, no network, no secrets — safe to import at module level.
Mirror style from backend/sanitiser/models.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field  # noqa: F401 — field available if subclasses need it


class AnalyserError(Exception):
    """Raised when all 3 model tiers fail or the response is unusable after all tiers."""


@dataclass(frozen=True)
class Categorisation:
    row_index: int
    category: str  # always a valid TAXONOMY member (coerced)


@dataclass(frozen=True)
class AnalysisResult:
    categories: dict[int, str]       # row_index -> TAXONOMY category (covers payload + dropped)
    category_totals: dict[str, str]  # category -> str(Decimal), computed LOCALLY from payload amounts
    summary: str                     # short LLM summary string ("" if absent/empty)
    flagged: list[int]               # row_index values the LLM flagged as unusual (filtered to payload)
    model_used: str                  # which tier actually answered ("" for the empty no-call case)
