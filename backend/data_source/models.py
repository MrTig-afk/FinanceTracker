"""models.py — core domain types for the data_source stage.

Transaction is the normalised output of every per-bank parser.
Bank identifies the source and is metadata only; it is never sent off-machine.
balance is SENSITIVE and LOCAL-ONLY: it is captured for the local dashboard's
opening/closing display and must never reach the sanitiser output, the audit
log, or any off-machine request.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class Bank(str, Enum):
    COMMBANK = "commbank"
    WESTPAC = "westpac"


@dataclass(frozen=True)
class Transaction:
    date: date           # transaction date, normalised
    description: str     # RAW description (outer whitespace stripped only); NOT sanitised here
    amount: Decimal      # signed: debit negative, credit positive
    bank: Bank           # source bank — lets later stages fingerprint/segregate without re-parsing
    balance: Decimal | None = None  # running balance AFTER this txn; LOCAL-ONLY, never sent off-machine
