"""Small helpers for reading published Excel workbooks defensively."""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import openpyxl

_ERROR_TOKENS = {"#DIV/0!", "#N/A", "#VALUE!", "#REF!", "#NAME?", "-", "(BLANK)"}
_NUMBER = re.compile(r"^-?\d+(\.\d+)?(e-?\d+)?$", re.IGNORECASE)


def read_sheets(path: Path) -> dict[str, list[tuple]]:
    """Every visible sheet as a list of rows of plain values.

    Reading stops after 200 empty rows in a row: one published sheet claims a
    million rows but holds a few hundred.
    """
    sheets: dict[str, list[tuple]] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # openpyxl warns about unsupported extensions
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet in book.worksheets:
            if sheet.sheet_state != "visible":
                continue
            rows: list[tuple] = []
            empty_run = 0
            for row in sheet.iter_rows(values_only=True):
                if any(value is not None and str(value).strip() for value in row):
                    empty_run = 0
                else:
                    empty_run += 1
                    if empty_run > 200:
                        break
                rows.append(row)
            while rows and not any(v is not None and str(v).strip() for v in rows[-1]):
                rows.pop()
            sheets[sheet.title] = rows
        book.close()
    return sheets


def is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def is_error_token(value: object) -> bool:
    return isinstance(value, str) and value.strip().upper() in _ERROR_TOKENS


def to_number(value: object) -> float | None:
    """A number from a cell, or None for blanks, error tokens and other text."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if _NUMBER.match(text):
            return float(text)
    return None


def cell(row: tuple, index: int) -> object:
    return row[index] if 0 <= index < len(row) else None
