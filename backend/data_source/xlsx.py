"""xlsx.py — Excel (.xlsx) upload support for the data-source stage.

Banks increasingly hand out .xlsx statement exports alongside CSV. Rather than
duplicate the per-bank column profiles, this module normalises an uploaded
workbook's FIRST worksheet into ordinary CSV text and hands it straight to the
existing content-based detection + per-bank parsers (detect_bank / parse_text).

That keeps the data source behind one interface: a format quirk is still a
one-place fix in the CSV parser profile, and xlsx inherits it for free.

Routing
-------
``upload_to_csv_text`` is the single entry point the pipeline calls. It inspects
the raw bytes (and filename) and:
  - xlsx  -> reads the first worksheet with openpyxl, emits CSV text
  - else  -> decodes the bytes as CSV text (UTF-8 w/ BOM, tolerant fallback)

An .xlsx is recognised by the ZIP local-file-header magic ``PK\\x03\\x04`` (every
.xlsx is a ZIP container) or, failing that, a ``.xlsx`` filename extension.

Privacy
-------
No disk IO and no network: the workbook is read from an in-memory ``BytesIO``.
openpyxl is already a project dependency (used by excel_builder) — no new deps.
"""
from __future__ import annotations

import csv
import datetime
import io
from decimal import Decimal

import openpyxl

# Every .xlsx is a ZIP archive; a ZIP always starts with this local-file header.
_XLSX_MAGIC = b"PK\x03\x04"


def looks_like_xlsx(content: bytes, filename: str | None = None) -> bool:
    """True when the upload should be read as an .xlsx workbook.

    Detects by the ZIP magic bytes first (authoritative — content beats the box
    it arrived in), then falls back to a ``.xlsx`` filename extension.
    """
    if content[:4] == _XLSX_MAGIC:
        return True
    if filename and filename.lower().endswith(".xlsx"):
        return True
    return False


def _cell_to_str(value) -> str:
    """Render one worksheet cell as the string a CSV parser would have seen.

    - None (empty cell)          -> "" (blank field)
    - datetime / date            -> day-first "DD/MM/YYYY" (matches parse_date)
    - int / float / Decimal      -> plain decimal string, never scientific
                                    notation (routed through Decimal(str(...))
                                    so a stored number parses like a CSV amount)
    - str                        -> unchanged (parsers strip per-column)
    - anything else              -> str(value) (defensive)
    """
    if value is None:
        return ""
    # datetime.datetime is a subclass of datetime.date — this catches both.
    if isinstance(value, datetime.date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, bool):
        # A bool would never be a valid date/amount; keep it out of the numeric
        # branch (bool is an int subclass) and let the parser skip the row.
        return str(value)
    if isinstance(value, (int, float, Decimal)):
        # Decimal(str(x)) avoids binary-float artefacts and scientific notation
        # for the magnitudes bank statements use.
        return str(Decimal(str(value)))
    if isinstance(value, str):
        return value
    return str(value)


def xlsx_to_csv_text(content: bytes) -> str:
    """Convert the FIRST worksheet of an .xlsx workbook to CSV text.

    Reads the workbook from in-memory bytes (read-only, values-only — cached
    values, not formulas). Every row becomes one CSV line, so the resulting text
    is indistinguishable from a CSV export to detect_bank / the per-bank parsers.

    Raises ValueError if the bytes are not a readable .xlsx workbook, so the
    caller can treat a corrupt/non-xlsx file as an unrecognised upload rather
    than crashing the batch.
    """
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(content), read_only=True, data_only=True
        )
    except Exception as exc:  # BadZipFile, KeyError, etc. — never leak details.
        raise ValueError("could not read xlsx workbook") from exc

    try:
        worksheet = workbook.worksheets[0]
    except IndexError:
        workbook.close()
        raise ValueError("xlsx workbook has no worksheets")

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    try:
        for row in worksheet.iter_rows(values_only=True):
            writer.writerow([_cell_to_str(cell) for cell in row])
    finally:
        workbook.close()

    return buffer.getvalue()


def upload_to_csv_text(content: bytes, filename: str | None = None) -> str:
    """Normalise a raw upload (CSV bytes OR .xlsx bytes) into CSV text.

    This is the single routing point the pipeline uses so the CSV code path is
    unchanged: xlsx is converted to CSV text and then flows through the exact
    same detect_bank + parse_text logic as a real CSV.

    CSV bytes are decoded UTF-8 (BOM-stripping) with a tolerant replace-mode
    fallback — identical to the pipeline's previous inline decode.
    """
    if looks_like_xlsx(content, filename):
        return xlsx_to_csv_text(content)

    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("utf-8", errors="replace")
