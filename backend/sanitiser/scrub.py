"""scrub.py — regex scrubbers + fail-closed residual re-scan (§7.5, FR-17/FR-21).

All patterns are compiled ONCE at module level as named, commented constants.
scrub_description() applies them in the documented ORDER (specific → generic) so that
generic digit-removal does not eat structure that specific patterns rely on.
has_residual_identifier() is the explicit fail-closed gate: it re-scans the already-
scrubbed string and returns True (→ DROP) if anything still looks identifying.

Pure functions only.  No I/O, no network calls, no side effects.
"""
from __future__ import annotations

import re

# ── Step 1: Email addresses (PayID email handles) ────────────────────────────
# Matches any "local@domain.tld" pattern.  Applied first so the '@' is gone
# before subsequent patterns could misidentify it.
RE_EMAIL = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# ── Step 2: Person names in P2P / PayID / PayTo transfer narratives ──────────
# Matches the person-name SPAN in Australian NPP/Osko/PayID/PayTo transfer
# descriptions.  Alternations are ordered most-specific to least-specific so
# the engine commits to the right branch before backtracking.
#
# Group 1 = "PayTo" keyword (kept)
# Group 2 = "Osko"  keyword (kept)
# Branches without a group replace the entire match with " " (the keyword
# preceding the name is OUTSIDE the match and therefore preserved).
#
# Coverage:
#   a) "From NAME... to PayID [Phone] LABEL"  — full PayID outgoing span
#   b) "From NAME... to"                      — plain P2P outgoing span
#   c) "to NAME... from"                      — P2P incoming span
#   d) "to PayID [Phone] LABEL"               — bare PayID handle label
#   e) "PayTo NAME..."   — group 1 kept; name words removed
#   f) "Osko  NAME..."   — group 2 kept; name words removed
#
# Notes:
# - [A-Za-z]+ with IGNORECASE matches both mixed-case and all-UPPER descriptions.
# - The greedy (?:\s+[A-Za-z]+)* backtracks correctly to find the final keyword
#   boundary; descriptions are short (<~120 chars) so backtracking is bounded.
# - _p2p_replacer() below handles the group-1/2 logic for the e/f branches.
RE_PAYID_P2P_NAME = re.compile(
    # a) "From NAME... to PayID [Phone] LABEL" (most specific — commit first)
    r"\bFrom\s+[A-Za-z]+(?:\s+[A-Za-z]+)*\s+to\s+PayID\s+(?:Phone\s+)?\S+"
    # b) "From NAME... to" (plain P2P outgoing; stops at the 'to' word boundary)
    r"|\bFrom\s+[A-Za-z]+(?:\s+[A-Za-z]+)*\s+to\b"
    # c) "to NAME... from" (P2P incoming)
    r"|\bto\s+[A-Za-z]+(?:\s+[A-Za-z]+)*\s+from\b"
    # d) standalone "to PayID [Phone] LABEL" (e.g. after earlier scrub exposed it)
    r"|\bto\s+PayID\s+(?:Phone\s+)?\S+"
    # e) "PayTo NAME..." — group 1 = the "PayTo" keyword to keep
    r"|\b(PayTo)\s+[A-Za-z]+(?:\s+[A-Za-z]+)*"
    # f) "Osko NAME..."  — group 2 = the "Osko" keyword to keep
    r"|\b(Osko)\s+[A-Za-z]+(?:\s+[A-Za-z]+)*",
    re.IGNORECASE,
)


def _p2p_replacer(m: re.Match) -> str:  # type: ignore[type-arg]
    """Replacement function for RE_PAYID_P2P_NAME.

    Branches (e) and (f) capture the keyword (group 1 or 2) that must be
    preserved for downstream categorisation; all other branches return ' '.
    """
    keyword = m.group(1) or m.group(2)
    return keyword + " " if keyword else " "


# ── Step 3: Phone numbers (AU mobile, AU landline, international) ─────────────
# Covers the most common PayID phone-handle formats used in AU banking:
#   • +61 4xx xxx xxx  (international mobile)
#   • 04xx xxx xxx     (domestic mobile, with or without separators)
#   • (0x) xxxx xxxx   (landline with area-code brackets)
#   • 0x xxxx xxxx     (landline without brackets)
#   • xxxx xxx xxx     (generic 10-digit mobile spacing)
RE_PHONE = re.compile(
    # International AU mobile: +61 4xx xxx xxx
    r"\+61[\s\-]?4\d{2}[\s\-]?\d{3}[\s\-]?\d{3}"
    # Domestic AU mobile: 04xx xxx xxx (with optional space/dash separators)
    r"|\b04\d{2}[\s\-]?\d{3}[\s\-]?\d{3}\b"
    # Landline with brackets: (0x) xxxx xxxx
    r"|\(0\d\)[\s\-]?\d{4}[\s\-]?\d{4}"
    # Landline without brackets: 0x xxxx xxxx (area codes 02-09)
    r"|\b0[2-9][\s\-]?\d{4}[\s\-]?\d{4}\b"
    # Generic 10-digit spacing pattern: xxxx xxx xxx
    r"|\b\d{4}[\s\-]\d{3}[\s\-]\d{3}\b",
)

# ── Step 4: BSB codes ─────────────────────────────────────────────────────────
# BSB format: 3 digits, dash, 3 digits (e.g. "063-000").
# Matched before generic digit patterns so the dash structure is recognised.
RE_BSB = re.compile(r"\b\d{3}-\d{3}\b")

# ── Step 5: Full card numbers ──────────────────────────────────────────────────
# Covers 16-digit card in the canonical 4×4 spaced/dashed format and any bare
# 13–19 digit run (Visa/Mastercard/Amex/longer schemes).
RE_CARD_FULL = re.compile(
    # 4×4 format with space or dash separator: 4111 1111 1111 1111
    r"\b\d{4}[\s\-]\d{4}[\s\-]\d{4}[\s\-]\d{4}\b"
    # Bare 13–19 digit run (no separators)
    r"|\b\d{13,19}\b",
)

# ── Step 6: Partial card markers ──────────────────────────────────────────────
# Catches the masked card placeholders banks embed in descriptions:
#   "Card xx7957"       — "Card " prefix then xx + digits
#   "xx9876"            — standalone xx-prefix partial card
# Case-insensitive: matches "CARD XX7957", "card xx7957", etc.
RE_CARD_PARTIAL = re.compile(
    r"\bCard\s+xx\d{3,}\b"
    r"|\bxx\d{3,}\b",
    re.IGNORECASE,
)

# ── Step 7: "Value Date: DD/MM/YYYY" trailing reference ───────────────────────
# Some banks append a value-date annotation to the description.
# Matches optional separators (colon, dash) and optional surrounding spaces.
RE_VALUE_DATE = re.compile(
    r"\bValue\s+Date\s*[:\-]?\s*\d{2}[/\-]\d{2}[/\-]\d{4}\b",
    re.IGNORECASE,
)

# ── Step 8: Explicit reference / serial labels ────────────────────────────────
# Matches "Ref:", "Reference:", "Receipt:", "Serial:" followed by the token.
# Both the label AND the token are removed so neither leaks.
RE_REF_CODE = re.compile(
    r"\b(?:Ref|Reference|Receipt|Serial)\s*:\s*[A-Za-z0-9]+",
    re.IGNORECASE,
)

# ── Step 9: Remaining long digit runs (6+) ────────────────────────────────────
# After all specific patterns have run, strip any leftover 6+ digit sequence
# (trailing serials, mobile references such as "MOBILE 1136212", etc.).
# The fail-closed re-scan (step 4+ below) catches 4-5 digit residue as well.
RE_LONG_DIGITS = re.compile(r"\b\d{6,}\b")

# ── Step 10: Whitespace collapse ──────────────────────────────────────────────
# Collapse any run of whitespace (spaces, tabs, non-breaking spaces) to a
# single ASCII space so the caller can .strip() to a clean string.
RE_MULTISPACE = re.compile(r"\s+")

# ── Fail-closed residual re-scan patterns (FR-21) ────────────────────────────
# These run AFTER all scrubbers.  If any match, the cleaned string is dropped.
# Defined after RE_BSB / RE_PHONE / RE_EMAIL so they can be reused directly.
#
# Explicit checks handled in has_residual_identifier() (not regex patterns):
#   • cleaned.strip() == "" — empty result
#   • "@" in cleaned        — email residue / PayID handle
#
# Regex residuals:
#   1. \d{4,}     — any 4+ consecutive digit run (belt-and-braces after 6+ scrub)
#   2. RE_BSB     — BSB re-match
#   3. RE_PHONE   — phone number re-match
#   4. RE_EMAIL   — email re-match
#   5. Transfer-name marker: payid/payto/osko/bpay keyword immediately followed by
#      a Capitalised word that looks like a leftover person name.
#      (keyword is matched case-insensitively via (?i:…); [A-Z] requires actual
#      uppercase so the check fires for mixed-case OR all-UPPER descriptions.)
RESIDUAL_PATTERNS: tuple[re.Pattern, ...] = (  # type: ignore[type-arg]
    # 1. Any 4+ consecutive digit run
    re.compile(r"\d{4,}"),
    # 2-4. Re-run specific identifier patterns
    RE_BSB,
    RE_PHONE,
    RE_EMAIL,
    # 5. Transfer-name marker: keyword + Capitalised/UPPER following word
    re.compile(r"(?i:\b(?:payid|payto|osko|bpay))\s+[A-Z][a-zA-Z]+"),
)


# ── Public scrubbing functions ─────────────────────────────────────────────────

def scrub_description(raw: str) -> str:
    """Apply scrubbers 1–10 in order.  Pure; returns the cleaned string (possibly '').

    Each scrubber substitutes its match with a single space; step 10 collapses
    whitespace and strips.  The returned string may be empty if all content was
    removed — callers must pass the result through has_residual_identifier().
    """
    text = RE_EMAIL.sub(" ", raw)                        # 1. emails
    text = RE_PAYID_P2P_NAME.sub(_p2p_replacer, text)   # 2. P2P names
    text = RE_PHONE.sub(" ", text)                       # 3. phone numbers
    text = RE_BSB.sub(" ", text)                         # 4. BSB codes
    text = RE_CARD_FULL.sub(" ", text)                   # 5. full card numbers
    text = RE_CARD_PARTIAL.sub(" ", text)                # 6. partial card markers
    text = RE_VALUE_DATE.sub(" ", text)                  # 7. value-date refs
    text = RE_REF_CODE.sub(" ", text)                    # 8. reference labels
    text = RE_LONG_DIGITS.sub(" ", text)                 # 9. long digit runs
    text = RE_MULTISPACE.sub(" ", text).strip()          # 10. collapse + trim
    return text


def has_residual_identifier(cleaned: str) -> bool:
    """Return True if `cleaned` must be DROPPED (FR-21 fail-closed gate).

    Conservative: prefer dropping over leaking.  Returns True if ANY of:
    • The string is empty (or whitespace-only) after scrubbing.
    • It still contains '@' (email/PayID handle residue).
    • It contains any run of 4+ consecutive digits (tighter than the 6+ scrub
      threshold — belt-and-braces second gate).
    • It re-matches RE_BSB, RE_PHONE, or RE_EMAIL.
    • It contains a transfer-name marker: a {payid, payto, osko, bpay} keyword
      immediately followed by a Capitalised word token (leftover person name).
    """
    if not cleaned.strip():
        return True
    if "@" in cleaned:
        return True
    for pattern in RESIDUAL_PATTERNS:
        if pattern.search(cleaned):
            return True
    return False
