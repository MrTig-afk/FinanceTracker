"""sanitiser — pre-LLM transaction scrubbing and fail-closed gate (§7.5, FR-16..FR-21).

Public API
----------
SanitisedTxn   : immutable triple (row_index, cleaned_description, amount); the ONLY thing
                 that is allowed to travel off-machine to the analyser (§7.6).
SanitiseResult : immutable batch result — payload of safe tuples, dropped indexes, run metadata.
sanitise       : reduce a list of Transaction objects to a SanitiseResult; applies regex
                 scrubbers, fail-closed residual re-scan, and writes an audit log locally.

The scrub helpers (scrub_description, has_residual_identifier) and the audit writer
(write_audit_log) are NOT exported here.  No other pipeline stage may reach around
sanitise() to access them directly via this package.
"""

from .core import sanitise
from .models import SanitisedTxn, SanitiseResult

__all__ = ["SanitisedTxn", "SanitiseResult", "sanitise"]
