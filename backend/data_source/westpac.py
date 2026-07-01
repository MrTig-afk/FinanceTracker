"""westpac.py — Westpac CSV parser (FR-9).

Westpac export format (header present):
    Column 0: Bank Account   (account number — DROPPED IMMEDIATELY, never stored)
    Column 1: Date           (DD-MM-YYYY or DD/MM/YYYY)
    Column 2: Narrative      (raw description)
    Column 3: Debit Amount   (positive string when populated, blank otherwise)
    Column 4: Credit Amount  (positive string when populated, blank otherwise)
    Column 5: Balance        (captured into Transaction.balance — LOCAL-ONLY, never sent off-machine)
    Column 6: Categories     (ignored)
    Column 7: Serial         (ignored; trailing columns may be absent)

Merge rules (FR-9):
    - Debit populated, Credit blank  -> amount = -debit   (debit negative)
    - Credit populated, Debit blank  -> amount = +credit  (credit positive)
    - Both blank                     -> skip (not a transaction)
    - Both populated (unexpected)    -> amount = credit - debit (net signed); skip if unparseable

Per-row tolerance: a row whose date or amount fails to parse is silently skipped (FR-11).
"""
from __future__ import annotations

from .base import BankParser
from .common import iter_csv_rows, parse_amount, parse_date, parse_optional_amount
from .models import Bank, Transaction


class WestpacParser(BankParser):
    """Parser for Westpac CSV exports."""

    bank = Bank.WESTPAC

    def parse(self, text: str) -> list[Transaction]:
        transactions: list[Transaction] = []
        for row in iter_csv_rows(text, has_header=True):
            # Need at minimum columns 0-4 (account, date, narrative, debit, credit).
            if len(row) < 5:
                continue

            # Column 0 is the account number.
            # It is dropped here and never assigned to any persisting variable.
            # We index the remaining columns directly without storing col 0.

            try:
                txn_date = parse_date(row[1])
            except ValueError:
                continue

            description = row[2].strip()
            debit_raw = row[3]
            credit_raw = row[4]
            # Column 5 (Balance) is captured LOCAL-ONLY below; never sent off-machine
            # (see models.py docstring). Columns 6 (Categories), 7 (Serial) stay ignored.
            balance = parse_optional_amount(row[5]) if len(row) > 5 else None

            debit_present = debit_raw.strip() != ""
            credit_present = credit_raw.strip() != ""

            if not debit_present and not credit_present:
                # Both empty — no transaction amount; skip.
                continue

            try:
                if debit_present and credit_present:
                    # Should not occur in practice; treat as net credit - debit.
                    amount = parse_amount(credit_raw) - parse_amount(debit_raw)
                elif debit_present:
                    amount = -parse_amount(debit_raw)
                else:
                    amount = parse_amount(credit_raw)
            except ValueError:
                continue

            transactions.append(
                Transaction(
                    date=txn_date,
                    description=description,
                    amount=amount,
                    bank=Bank.WESTPAC,
                    balance=balance,
                )
            )
        return transactions
