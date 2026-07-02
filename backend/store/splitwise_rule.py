"""splitwise_rule.py — deterministic categoriser for self-tagged Splitwise transfers.

Splitwise settle-ups are bank transfers (PayID/Osko) with no merchant name, so the
LLM has nothing to categorise on. The owner instead writes a self-authored tag into
the transfer reference, e.g. "Splitwise utilities", "Splitwise food", "Splitwise
settle" — whatever the payment platform appends around it (dates, transfer ids, a
friend's name) is noise.

This tag CANNOT be read by the LLM: the sanitiser runs before the off-machine call
and, for these P2P descriptions, either greedily deletes the tag words or fail-closed
drops the whole row. So the tag is matched here, on the RAW local description, before
sanitise() — and matched rows are categorised deterministically and excluded from the
LLM payload (privacy: the reference may contain a friend's name).

No IO, no network, no secrets. Operates on the local (unsanitised) description string,
which never leaves the machine.
"""
from __future__ import annotations

import re

# Recognised keyword (uppercased) -> taxonomy label. Extend as needed; any Splitwise
# tag whose word is NOT here (or a bare "Splitwise") deliberately falls to "Other".
_KEYWORDS: dict[str, str] = {
    "UTILITIES": "Utilities",
    "FOOD": "Dining Out",
    "DINING": "Dining Out",
    "SETTLE": "Other",
    "SETTLED": "Other",
    "SETTLEMENT": "Other",
    "PAYBACK": "Other",
}

# Anchor on a standalone "SPLITWISE" token, then tolerate space/colon/underscore/
# hyphen separators and capture the following word (optional — a bare "Splitwise" is
# still a valid, deliberate tag). Case-insensitive.
#
# Letter-only lookarounds (not \b) are deliberate: \b treats "_" as a word char, so
# "\bSPLITWISE\b" would FAIL to match "SPLITWISE_SETTLE". (?<![A-Za-z]) / (?![A-Za-z])
# reject a letter on either side — so "MYSPLITWISEACCOUNT" does not match — while still
# allowing an underscore/hyphen/colon/digit separator immediately after the token.
_TAG_RE = re.compile(
    r"(?<![A-Za-z])SPLITWISE(?![A-Za-z])[\s:_\-]*([A-Za-z]+)?", re.IGNORECASE
)

_FALLBACK = "Other"


def match_splitwise_tag(raw_description: str | None) -> str | None:
    """Return a taxonomy label if the raw description carries a 'Splitwise <word>' tag.

    - No standalone "Splitwise" token -> None (row flows to the normal LLM path).
    - "Splitwise <recognised word>" -> the mapped taxonomy label.
    - "Splitwise <unrecognised word>" or a bare "Splitwise" -> "Other" (by design:
      it is unambiguously the owner's own transfer, and should never reach the LLM).

    Matching is case-insensitive and finds the tag anywhere in the string, so bank/
    platform text before or after it (including a friend's name) does not defeat it.
    None or empty string returns None.
    """
    if not raw_description:
        return None
    m = _TAG_RE.search(raw_description)
    if m is None:
        return None
    keyword = (m.group(1) or "").upper()
    return _KEYWORDS.get(keyword, _FALLBACK)
