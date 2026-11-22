from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]


def write_workbook(path: Path, sheets: dict[str, list[list]]) -> Path:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    book.save(path)
    return path


@pytest.fixture
def workbook(tmp_path):
    def make(name: str, sheets: dict[str, list[list]]) -> Path:
        return write_workbook(tmp_path / name, sheets)

    return make
