"""category_context.py — fixed v1 category-context seed data (D1 / D2).

CategoryContext is the per-category hint row shown on the "Category context" screen.
Name, colour, and position are FIXED to the canonical TAXONOMY order (D1) — only the
`hints` field is ever user-editable. DEFAULT_CONTEXT seeds real example hint text
(D2) so the generated-prompt preview shows real content on first load.

No IO, no network, no secrets. Safe to import at module level.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CategoryContext:
    """One category-context row: fixed name/color/position, user-editable hints."""

    name: str
    color: str      # hex, e.g. "#57b26f"
    hints: str      # user's own merchant labels/notes (D2: seeded with example text)
    position: int   # 0-based display order


# Canonical seed: TAXONOMY order, hex colours copied verbatim from
# frontend/src/summary.js CATEGORY_COLORS (so card dots match the donut legend),
# and hints copied VERBATIM from the mockup pool array (D2), with the one em dash
# (Utilities hint) replaced by a comma per the owner's no-em-dash preference.
DEFAULT_CONTEXT: tuple[CategoryContext, ...] = (
    CategoryContext(
        name="Groceries",
        color="#57b26f",
        hints="Woolworths, Coles, Aldi, IGA, Harris Farm, the local butcher and greengrocer.",
        position=0,
    ),
    CategoryContext(
        name="Utilities",
        color="#4a90d9",
        hints=(
            "AGL, Origin Energy, EnergyAustralia, Red Energy, Alinta, electricity, "
            "gas and water. Internet and mobile: MORE, Aussie Broadband, Telstra, "
            "Optus, TPG, iiNet, Vodafone, Aldi Mobile, Belong."
        ),
        position=1,
    ),
    CategoryContext(
        name="Rent",
        color="#9b6cd4",
        hints=(
            "Regular rent, usually monthly (sometimes weekly or fortnightly), paid to a "
            "real estate agent, property manager or landlord. Common agents and platforms: "
            "MICM, Apartment Living, Ray White, LJ Hooker, Harcourts, Barry Plant, McGrath, "
            "realestate.com.au, Domain, Ailo. Includes bond and deposit payments."
        ),
        position=2,
    ),
    CategoryContext(
        name="Dining Out",
        color="#e0913f",
        hints=(
            "Cafes, restaurants, pubs and takeaway, including named eateries even "
            "when the name mentions food ingredients (e.g. 'spices', 'meats', "
            "'bakery'). Rozzi's, Southbank Spices. Many cafes and takeaways bill "
            "via Square, shown as 'SQ *<merchant>'. Uber Eats, DoorDash, Menulog, "
            "coffee shops."
        ),
        position=3,
    ),
    CategoryContext(
        name="Transport",
        color="#34a7a3",
        hints="Opal, Myki, Go Card. Fuel: BP, Shell, Ampol, 7-Eleven. Uber, tolls (Linkt), parking.",
        position=4,
    ),
    CategoryContext(
        name="Entertainment",
        color="#d96ba6",
        hints="Cinemas (Hoyts, Event), concerts, Ticketek, sport, hobbies and games.",
        position=5,
    ),
    CategoryContext(
        name="Subscriptions",
        color="#6f6bd8",
        hints=(
            "Netflix, Spotify, Disney+, Amazon Prime, iCloud storage, gym memberships. "
            "Also AI tools and services like OpenAI (ChatGPT), Anthropic (Claude), "
            "Perplexity, Google Gemini, GitHub Copilot, Cursor."
        ),
        position=6,
    ),
    CategoryContext(
        name="Income",
        color="#8BC34A",
        hints="Salary and wages, refunds, interest, transfers in.",
        position=7,
    ),
    CategoryContext(
        name="Other",
        color="#a89f8c",
        hints="Anything that doesn't clearly belong to another category.",
        position=8,
    ),
)
