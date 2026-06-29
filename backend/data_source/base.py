"""base.py — BankParser ABC and the data-source dispatch interface.

This module is the single seam between the pipeline and per-bank parsers.
Replacing or adding a bank means adding a new BankParser subclass and
registering it here — no other stage needs to change.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from .models import Bank, Transaction


class BankParser(ABC):
    """Abstract base for per-bank CSV parsers.

    Subclasses set the class-level ``bank`` attribute and implement ``parse``.
    The contract is text-in / list[Transaction]-out so tests never need disk
    access and no sensitive temp files are created.
    """

    bank: Bank

    @abstractmethod
    def parse(self, text: str) -> list[Transaction]: ...


# Registry populated lazily (inside _register_parsers) to avoid circular
# imports: commbank.py and westpac.py import BankParser from this module,
# so they cannot be imported at module-load time before BankParser is defined.
_PARSERS: dict[Bank, type[BankParser]] = {}


def _register_parsers() -> None:
    """Import parser subclasses and populate _PARSERS.

    Called once at the bottom of this module after BankParser is defined.
    The deferred import breaks the potential circular-import cycle.
    """
    from .commbank import CommBankParser  # noqa: PLC0415
    from .westpac import WestpacParser    # noqa: PLC0415

    _PARSERS[Bank.COMMBANK] = CommBankParser
    _PARSERS[Bank.WESTPAC] = WestpacParser


_register_parsers()


def get_parser(bank: Bank | str) -> BankParser:
    """Return an instantiated parser for the given bank.

    Accepts a Bank enum value or its string value ('commbank' / 'westpac'),
    case-insensitive.  Raises ValueError on unknown bank.
    """
    if isinstance(bank, str):
        try:
            bank = Bank(bank.lower())
        except ValueError:
            valid = [b.value for b in Bank]
            raise ValueError(
                f"Unknown bank: {bank!r}. Valid values: {valid}"
            )
    parser_cls = _PARSERS.get(bank)
    if parser_cls is None:
        raise ValueError(f"No parser registered for bank: {bank!r}")
    return parser_cls()


def parse_text(text: str, bank: Bank | str) -> list[Transaction]:
    """Parse CSV text for the given bank and return a list of Transactions."""
    return get_parser(bank).parse(text)


def parse_file(path: str | os.PathLike, bank: Bank | str) -> list[Transaction]:
    """Read a CSV file from disk and delegate to parse_text.

    Opens with newline='' so csv handles all line-ending variants (RFC-4180).
    Uses errors='replace' as a tolerant fallback for non-UTF-8 bytes.
    This is the only function in this stage that touches disk; everything
    else operates on in-memory text to avoid sensitive temp files.
    """
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return parse_text(text, bank)
