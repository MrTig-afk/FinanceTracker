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


def build_context_prompt(categories: Sequence[CategoryContext]) -> str:
    """Return the TAXONOMY & CONTEXT preamble string. Byte-identical to the JS mirror.

    Header line, then the separator line, then a single '\\n', then the first
    '- name' entry. Each entry has a 4-space indent before the hints line (or
    '(no extra context)' when hints are empty/whitespace-only). Hints whitespace
    is collapsed to single spaces and trimmed. Categories are joined by a blank
    line ('\\n\\n'). An empty name falls back to 'Untitled' (kept for parity with
    the mockup; with D1 the 9 canonical names are always present in production).
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
    return "\n".join(head) + "\n" + "\n\n".join(bodies)
