"""models.py — core domain types for the data_source stage.

Transaction is the normalised output of every per-bank parser.
Bank identifies the source and is metadata only; it is never sent off-machine.
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
