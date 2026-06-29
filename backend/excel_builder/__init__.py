"""excel_builder — monthly .xlsx workbook builder for FinanceTracker (§7.8, FR-30).

Builds a financetracker-YYYY-MM.xlsx with two sheets:
  - "Transactions" — raw Date / Description / Amount / Category per MonthRow.
  - "Summary"      — per-category totals and a Net row.

Privacy note
------------
The Excel holds FULL LOCAL DATA (raw descriptions, real amounts).
This is the owner's local/own-Drive archive, NOT the sanitised off-machine payload.
*.xlsx is gitignored and must never be committed to the repository.

No filesystem or network access occurs on a bare ``import backend.excel_builder``.
"""
from __future__ import annotations

from .builder import build_workbook, resolve_output_dir

__all__ = [
    "build_workbook",
    "resolve_output_dir",
]
