"""analyser — LLM-based transaction categorisation via OpenRouter (§7.6, FR-22..FR-26).

Public API
----------
categorise      : Accepts a SanitiseResult (or Sequence[SanitisedTxn]) and returns an
                  AnalysisResult.  NEVER accepts raw Transaction data.
OpenRouterClient: 3-tier fallback HTTP client.  Reads config from .env on construction;
                  importing this package does NOT touch the network or require env vars.
AnalysisResult  : Immutable result dataclass: categories, category_totals (computed
                  locally), summary, flagged, model_used.
AnalyserError   : Raised when all model tiers fail or the response is unusable.

Privacy guarantee
-----------------
The only data that leaves this machine is the sanitised tuple
(row_index, cleaned_description, amount) via OpenRouter.  No raw Transaction fields,
no dates, no bank identifiers, no balances, no account numbers.

Importing this package is IO-free and network-free.  Env vars are resolved only when
OpenRouterClient() is constructed (lazy; mirrors backend/store and backend/sanitiser).
"""

from .analyser import categorise
from .client import OpenRouterClient
from .models import AnalysisResult, AnalyserError

__all__ = ["categorise", "OpenRouterClient", "AnalysisResult", "AnalyserError"]
