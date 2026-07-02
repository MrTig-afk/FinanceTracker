"""context.py — canonical TAXONOMY & CONTEXT preamble builder.

SINGLE SOURCE OF TRUTH for the preamble format prepended to the analyser's system
prompt. Logic copied exactly from the mockup buildPrompt()
(`Category Context.dc.html` lines 147-157); the JS mirror in
frontend/src/categoryContext.js must stay byte-identical to this function, and
both are pinned to the same golden fixture in their respective test suites.

No IO, no network, no secrets. Safe to import at module level.
"""
from __future__ import annotations

import re
from typing import Sequence

from backend.store import CategoryContext

# Header for the few-shot examples block appended when the owner has made manual
# category corrections. Kept SEPARATE from the TAXONOMY & CONTEXT head so the
# no-corrections output stays byte-identical to the JS mirror / golden fixture.
_EXAMPLES_HEADER = "Examples of how the owner has corrected categories:"


def _corrections_block(recent_corrections: Sequence[tuple[str, str]]) -> str:
    """Render recent corrections as a deterministic few-shot examples block, or ''.

    Each correction becomes one 'cleaned_description -> Category' line, in the order
    given (the store supplies them newest-first and already capped). Only the safe
    cleaned_description and the category appear — never a raw description. Returns ''
    when there are no corrections so the preamble is unchanged (back-compat).
    """
    if not recent_corrections:
        return ""
    lines = [f"{cleaned} -> {category}" for cleaned, category in recent_corrections]
    return _EXAMPLES_HEADER + "\n" + "\n".join(lines)


def build_context_prompt(
    categories: Sequence[CategoryContext],
    recent_corrections: Sequence[tuple[str, str]] = (),
) -> str:
    """Return the TAXONOMY & CONTEXT preamble string. Byte-identical to the JS mirror.

    Header line, then the separator line, then a single '\\n', then the first
    '- name' entry. Each entry has a 4-space indent before the hints line (or
    '(no extra context)' when hints are empty/whitespace-only). Hints whitespace
    is collapsed to single spaces and trimmed. Categories are joined by a blank
    line ('\\n\\n'). An empty name falls back to 'Untitled' (kept for parity with
    the mockup; with D1 the 9 canonical names are always present in production).

    recent_corrections (few-shot learning): an optional sequence of
    (cleaned_description, category) tuples (newest first, already capped by the
    store). When non-empty, a short "Examples of how the owner has corrected
    categories:" block of 'cleaned_description -> Category' lines is appended after
    a blank line. When empty (the default), the output is byte-identical to the
    previous single-argument behaviour, so the JS mirror and golden fixture still
    hold. Only the cleaned_description + category travel — never a raw description.
    """
    head = [
        "TAXONOMY & CONTEXT",
        "------------------",
    ]
    bodies = []
    for cat in categories:
        h = re.sub(r"\s+", " ", (cat.hints or "")).strip()
        bodies.append(
            "- " + (cat.name or "Untitled") + "\n    " + (h if h else "(no extra context)")
        )
    preamble = "\n".join(head) + "\n" + "\n\n".join(bodies)

    block = _corrections_block(recent_corrections)
    if block:
        return preamble + "\n\n" + block
    return preamble
