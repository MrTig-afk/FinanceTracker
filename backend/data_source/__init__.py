"""data_source — per-bank CSV parsers for FinanceTracker (v1).

Public API
----------
Transaction  : normalised, immutable transaction record (frozen dataclass)
Bank         : supported bank enum (COMMBANK, WESTPAC)
BankParser   : abstract base class for per-bank parsers
get_parser   : factory — Bank | str -> BankParser instance
parse_text   : parse CSV text in-memory for a given bank
parse_file   : read a file from disk and parse (the only disk-touching function)

All other pipeline stages import exclusively through this public surface so
the parsers can be swapped (e.g. for a paid data feed) without touching
sanitiser, store, analyser, or any other stage.
"""

from .base import BankParser, get_parser, parse_file, parse_text
from .detect import detect_bank
from .models import Bank, Transaction

__all__ = [
    "Transaction",
    "Bank",
    "BankParser",
    "get_parser",
    "parse_file",
    "parse_text",
    "detect_bank",
]
