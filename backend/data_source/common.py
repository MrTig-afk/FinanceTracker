"""common.py — tolerant shared parsing helpers (FR-11).

These helpers are bank-agnostic and are imported by each per-bank parser profile.
No bank-specific logic lives here.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterator


# Ordered list of day-first date formats to try.
# Accepts both '-' and '/' separators, and 2-digit year variants.
_DATE_FORMATS: list[str] = [
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d-%m-%y",
    "%d/%m/%y",
]


def parse_date(raw: str) -> date:
    """Parse a day-first date string into a datetime.date.

    Accepts '-' and '/' as separators; 4-digit and 2-digit year forms.
    Raises ValueError (containing the offending string, no surrounding row
    context that could be sensitive) if none of the tried formats match.
    """
    cleaned = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {cleaned!r}")


def parse_amount(raw: str) -> Decimal:
    """Parse a currency string into a Decimal.

    Strips whitespace, surrounding quotes, a leading '$' or '-$', and
    thousands-separator commas (e.g. '$1,234.56' -> Decimal('1234.56')).
    Preserves a leading '-' for debits.
    Never converts through float; uses Decimal() directly.
    Raises ValueError for empty/whitespace-only input or unparseable strings.
    """
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("Empty amount string")

    # Remove surrounding double-quotes if present (can occur in some exports).
    if len(cleaned) >= 2 and cleaned[0] == '"' and cleaned[-1] == '"':
        cleaned = cleaned[1:-1].strip()
        if not cleaned:
            raise ValueError("Empty amount string after removing quotes")

    # Handle negative with currency symbol: '-$40.00' -> '-40.00'
    if cleaned.startswith("-$"):
        cleaned = "-" + cleaned[2:]
    elif cleaned.startswith("$"):
        cleaned = cleaned[1:]

    # Remove thousands separators (commas not adjacent to digits are unusual
    # but harmless to remove here — Decimal will reject anything unparseable).
    cleaned = cleaned.replace(",", "")

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Cannot parse amount: {raw!r}")


def parse_optional_amount(raw: str) -> Decimal | None:
    """Parse a balance cell to Decimal, or None if blank/whitespace/unparseable.

    Never raises: a missing or malformed balance must not drop the transaction.
    Reuses parse_amount's $/comma/quote handling.
    """
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return parse_amount(raw)
    except ValueError:
        return None


def iter_csv_rows(text: str, *, has_header: bool) -> Iterator[list[str]]:
    """Yield non-blank rows from CSV text, optionally skipping the header.

    Uses csv.reader for RFC-4180 compliance (handles quoted fields containing
    commas and embedded newlines). Rows where every cell is empty or whitespace-
    only are silently skipped. If has_header is True, the first non-blank row
    is consumed and discarded as the header.

    Yields list[str] with cells unstripped (callers decide per-column handling).
    """
    reader = csv.reader(io.StringIO(text, newline=""))
    header_consumed = not has_header  # if no header, act as though already consumed
    for row in reader:
        # Skip blank / whitespace-only rows.
        if not row or all(cell.strip() == "" for cell in row):
            continue
        if not header_consumed:
            header_consumed = True
            continue  # discard the header row
        yield row
