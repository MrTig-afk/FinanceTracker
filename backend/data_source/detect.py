"""detect.py — content-based bank detection (which parser a CSV needs).

The upload UI has a CommBank box and a Westpac box, but the file that lands in
either box should be parsed by the profile that matches its ACTUAL contents, not
the box it was dropped in. This module inspects the first few rows and decides.

The two v1 formats are unambiguous on the first line:
  - CommBank : NO header row; column 0 is a date.
  - Westpac  : header row present; column 0 is an account number (not a date),
               and the following data rows carry the date in column 1.

detect_bank returns None when the text matches neither shape, so the pipeline can
reject an unrecognised file with a clear message instead of silently ingesting
nothing. No IO, no network. Operates on already-decoded text only.
"""
from __future__ import annotations

from .common import iter_csv_rows, parse_date
from .models import Bank

# How many leading rows to inspect before giving up. A couple is plenty; this
# just bounds work on a huge file and tolerates a stray blank line.
_SNIFF_ROWS = 5


def _is_date(cell: str) -> bool:
    """True if the cell parses as a day-first date (tolerant of '-' or '/')."""
    try:
        parse_date(cell)
        return True
    except ValueError:
        return False


def detect_bank(text: str) -> Bank | None:
    """Detect which bank profile a CSV belongs to from its contents.

    Returns Bank.COMMBANK, Bank.WESTPAC, or None when neither shape matches.
    Detection is independent of which upload box the file came from.
    """
    rows: list[list[str]] = []
    for row in iter_csv_rows(text, has_header=False):
        rows.append(row)
        if len(rows) >= _SNIFF_ROWS:
            break

    if not rows:
        return None

    # CommBank: no header, so the very first row already starts with a date.
    first = rows[0]
    if len(first) >= 3 and _is_date(first[0]):
        return Bank.COMMBANK

    # Westpac: first row is a header; a data row has a non-date account number in
    # column 0 and the date in column 1.
    for row in rows[1:]:
        if len(row) >= 5 and not _is_date(row[0]) and _is_date(row[1]):
            return Bank.WESTPAC

    return None
