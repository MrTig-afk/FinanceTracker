"""fuel_rule.py — merchant classifier for the small-fuel-stop reclassification rule.

Some merchants sell BOTH fuel and food (BP, 7-Eleven, Ampol convenience stores, etc.).
A bank feed is merchant-level, not item-level, so a small charge at one of these is
almost always a snack/coffee, not fuel. This module decides whether a stored, local
transaction description belongs to such a fuel/convenience chain.

It deliberately matches ONLY fuel/convenience chains, so pure public-transport
merchants (Opal, Myki, Skybus, etc.) are never touched: they are simply not on the
allowlist, so is_fuel_convenience() returns False for them.

No IO, no network, no secrets. Operates on the local (unsanitised) description string,
which never leaves the machine.
"""
from __future__ import annotations

import re

# Fuel / convenience chains (mostly Australian) whose small spends are usually snacks.
# Each entry is a whole-word regex fragment so short tokens like "BP" do not match
# substrings such as "BPAY". Case-insensitive. Extend this list as needed.
_FUEL_PATTERNS: tuple[str, ...] = (
    r"BP",                    # matched as a whole word only (not BPAY)
    r"7[\s-]?ELEVEN",         # 7-ELEVEN, 7 ELEVEN, 7ELEVEN
    r"SEVEN\s+ELEVEN",
    r"AMPOL",
    r"CALTEX",
    r"COLES\s+EXPRESS",
    r"REDDY(?:\s+EXPRESS)?",  # Reddy Express (Coles Express rebrand)
    r"SHELL",
    r"MOBIL",                 # \b guards against matching "MOBILE"
    r"UNITED\s+PETROLEUM",
    r"METRO\s+PETROLEUM",
    r"OTR",                   # On The Run (SA/interstate)
    r"PUMA",                  # Puma Energy
)

# Join with word boundaries so each token must stand alone.
_FUEL_RE = re.compile(
    r"\b(?:" + "|".join(_FUEL_PATTERNS) + r")\b",
    re.IGNORECASE,
)


def is_fuel_convenience(description: str | None) -> bool:
    """Return True if the description names a fuel/convenience chain.

    Whole-word, case-insensitive match against the allowlist. Pure public-transport
    merchants (Opal, Myki, Skybus) and lookalikes (BPAY, MOBILE) return False.
    None or empty string returns False.
    """
    if not description:
        return False
    return _FUEL_RE.search(description) is not None
