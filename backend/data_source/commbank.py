"""commbank.py — CommBank NetBank CSV parser (FR-8).

CommBank export format (no header row):
    Column 0: Date          (DD-MM-YYYY or DD/MM/YYYY)
    Column 1: Amount        (signed float-string; debit negative, credit positive)
    Column 2: Description   (raw narrative)
    Column 3: Balance       (read past, never stored — sensitive, out of FR-10 scope)

A row with fewer than 3 columns, or a row whose date or amount fails to parse,
is silently skipped so one bad row never aborts a whole file (FR-11).
"""
from __future__ import annotations

from .base import BankParser
from .common import iter_csv_rows, parse_amount, parse_date
from .models import Bank, Transaction


class CommBankParser(BankParser):
    """Parser for CommBank NetBank desktop CSV exports."""

    bank = Bank.COMMBANK

    def parse(self, text: str) -> list[Transaction]:
        transactions: list[Transaction] = []
        for row in iter_csv_rows(text, has_header=False):
            # Need at least date, amount, description.
            if len(row) < 3:
                continue
            try:
                txn_date = parse_date(row[0])
                amount = parse_amount(row[1])
            except ValueError:
                # Malformed date or amount — skip this row, keep going.
                continue
            description = row[2].strip()
            # row[3] is balance — read past but never assigned to a variable
            # that persists; it is sensitive and outside the FR-10 shape.
            transactions.append(
                Transaction(
                    date=txn_date,
                    description=description,
                    amount=amount,
                    bank=Bank.COMMBANK,
                )
            )
        return transactions
