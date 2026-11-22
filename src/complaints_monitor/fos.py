"""Parser for the Financial Ombudsman Service's half-yearly business complaints data.

Each workbook has a sheet of new cases by product category and a sheet of the
share of resolved cases upheld (found in the consumer's favour). The number of
resolved cases behind each percentage is not published. Header rows are spread
over one to three rows and their wording changes between releases, so columns
are recognised from the combined header text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .names import clean_name, name_key
from .periods import normalise_label
from .workbook import cell, is_blank, read_sheets, to_number

# Ombudsman category -> FCA product group it corresponds to (None: no FCA equivalent).
CATEGORY_TO_PRODUCT_GROUP = {
    "Banking & credit": "Banking and credit cards",
    "Mortgages & home finance": "Home finance",
    "General insurance & pure protection": "Insurance & pure protection",
    "PPI": "Insurance & pure protection",
    "Investments": "Investments",
    "Decumulation, life & pensions": "Decumulation & pensions",
    "Funeral planning services": None,
    "Product type not recorded": None,
}


def classify_column(header: str) -> str | None:
    """What a column holds, from its combined header text."""
    text = re.sub(r"\s+", " ", header).strip().lower()
    if not text:
        return None
    if "plevin" in text:
        return None
    if "business name" in text:
        return "business_name"
    if "business group" in text:
        return "business_group"
    if "proactive" in text:
        return "proactive_settlements"
    if "banking" in text:
        return "Banking & credit"
    if "mortgage" in text:
        return "Mortgages & home finance"
    if "general insurance" in text:
        return "General insurance & pure protection"
    if re.search(r"\bppi\b", text):
        return "PPI"
    if "investment" in text:
        return "Investments"
    if "decumulation" in text:
        return "Decumulation, life & pensions"
    if "funeral" in text:
        return "Funeral planning services"
    if "product type" in text and "not" in text:
        return "Product type not recorded"
    if "total" in text or "upheld" in text:
        return "total"  # in H1 2021 the overall rate column has no "Total" in its header
    return None


def _first_data_row(rows: list[tuple]) -> int | None:
    for index, row in enumerate(rows):
        first = cell(row, 0)
        if is_blank(first) or "business name" in str(first).lower():
            continue
        if any(to_number(value) is not None for value in row[2:]):
            return index
    return None


def _columns(rows: list[tuple], data_start: int) -> dict[int, str]:
    width = max(len(row) for row in rows[: data_start + 1])
    columns: dict[int, str] = {}
    for index in range(width):
        header = " ".join(
            str(cell(row, index)) for row in rows[:data_start] if not is_blank(cell(row, index))
        )
        kind = classify_column(header)
        if kind is not None and kind not in columns.values():
            columns[index] = kind
    return columns


@dataclass
class ParsedOmbudsman:
    by_category: pd.DataFrame
    by_business: pd.DataFrame
    published_totals: pd.DataFrame
    issues: list[dict]


def _read_sheet(rows: list[tuple], measure: str, label: str, issues: list[dict]):
    """Long records (business, category, value), per-business totals, and published total rows."""
    start = _first_data_row(rows)
    if start is None:
        issues.append(
            {
                "source": "Ombudsman",
                "period_label": label,
                "sheet": measure,
                "firm_name": "",
                "issue": "sheet layout not recognised",
                "detail": "",
            }
        )
        return [], [], []
    columns = _columns(rows, start)
    name_col = next((i for i, k in columns.items() if k == "business_name"), 0)
    group_col = next((i for i, k in columns.items() if k == "business_group"), 1)
    records, totals, published = [], [], []
    for row in rows[start:]:
        raw_name = cell(row, name_col)
        if is_blank(raw_name):
            continue
        name = clean_name(raw_name)
        lowered = name.lower()
        if lowered.startswith(("totals of", "overall total", "total")):
            for index, kind in columns.items():
                value = to_number(cell(row, index))
                if value is not None and kind not in ("business_name", "business_group"):
                    published.append(
                        {
                            "period_label": label,
                            "measure": measure,
                            "row": name,
                            "category": kind,
                            "value": value,
                        }
                    )
            continue
        if not any(to_number(value) is not None for value in row[2:]):
            continue  # a footnote, or a business listed with no figures
        group = cell(row, group_col)
        base = {
            "period_label": label,
            "business_name": name,
            "business_group": clean_name(group) if not is_blank(group) else None,
        }
        for index, kind in columns.items():
            if kind in ("business_name", "business_group"):
                continue
            value = to_number(cell(row, index))
            if value is None:
                continue
            if measure == "pct_upheld" and kind not in ("proactive_settlements",) and not 0 <= value <= 1:
                issues.append(
                    {
                        "source": "Ombudsman",
                        "period_label": label,
                        "sheet": measure,
                        "firm_name": name,
                        "issue": "percentage outside 0 to 100%",
                        "detail": f"{kind}: {value}",
                    }
                )
                continue
            if kind in ("total", "proactive_settlements"):
                totals.append({**base, "field": kind, "value": value})
            else:
                records.append({**base, "category": kind, "value": value})
    return records, totals, published


def _drop_repeats(
    frame: pd.DataFrame, keys: list[str], sheet: str, label: str, issues: list[dict]
) -> pd.DataFrame:
    """A business listed twice in one sheet: keep the first listing and log the repeat."""
    if frame.empty or "category" not in frame:
        return frame
    repeated = frame.duplicated(keys, keep="first")
    for row in frame[repeated].itertuples():
        issues.append(
            {
                "source": "Ombudsman",
                "period_label": label,
                "sheet": sheet,
                "firm_name": row.business_name,
                "issue": "business listed twice; repeat ignored",
                "detail": row.category,
            }
        )
    return frame[~repeated]


def parse_workbook(path: Path, period_label: str) -> ParsedOmbudsman:
    label = normalise_label(period_label)
    issues: list[dict] = []
    new_sheet = resolved_sheet = None
    for title, rows in read_sheets(Path(path)).items():
        lowered = title.lower()
        if lowered.startswith("new"):
            new_sheet = rows
        elif lowered.startswith("resolved"):
            resolved_sheet = rows
    if new_sheet is None or resolved_sheet is None:
        raise ValueError(f"{Path(path).name}: expected a 'New ...' and a 'Resolved ...' sheet")

    new_records, new_totals, new_published = _read_sheet(new_sheet, "new_cases", label, issues)
    res_records, res_totals, res_published = _read_sheet(resolved_sheet, "pct_upheld", label, issues)

    # The two sheets do not always spell a business the same way ("Bank of
    # Scotland Plc" and "Bank of Scotland plc"), so they are joined on the
    # normalised name and the spelling from the new-cases sheet is kept.
    def frame(records: list[dict], value: str) -> pd.DataFrame:
        out = pd.DataFrame(records).rename(columns={"value": value})
        if out.empty:
            return pd.DataFrame(columns=["period_label", "business_name", "business_group", "business_key"])
        out["business_key"] = out["business_name"].map(name_key)
        return out

    keys = ["period_label", "business_key", "category"]
    new = _drop_repeats(frame(new_records, "new_cases"), keys, "new cases", label, issues)
    resolved = _drop_repeats(frame(res_records, "pct_upheld"), keys, "resolved cases", label, issues)
    names = pd.concat([new, resolved])[["business_key", "business_name", "business_group"]].drop_duplicates(
        "business_key"
    )
    by_category = new[keys + ["new_cases"]].merge(resolved[keys + ["pct_upheld"]], on=keys, how="outer")
    by_category = by_category.merge(names, on="business_key", how="left")
    by_category["product_group"] = by_category["category"].map(CATEGORY_TO_PRODUCT_GROUP)
    by_category["source_file"] = Path(path).name

    by_business = pd.DataFrame(columns=["period_label", "business_name", "business_key"])
    if new_totals or res_totals:
        key = ["period_label", "business_key"]

        def field(records: list[dict], name: str, column: str) -> pd.DataFrame:
            out = pd.DataFrame(
                records, columns=["period_label", "business_name", "business_group", "field", "value"]
            )
            out = out[out["field"] == name].rename(columns={"value": column})
            out["business_key"] = out["business_name"].map(name_key)
            return out.drop_duplicates(key)[key + [column]]

        by_business = (
            field(new_totals, "total", "total_new_cases")
            .merge(field(res_totals, "total", "total_pct_upheld"), on=key, how="outer")
            .merge(field(res_totals, "proactive_settlements", "proactive_settlements"), on=key, how="outer")
            .merge(names, on="business_key", how="left")
        )
        by_business["source_file"] = Path(path).name

    published = pd.DataFrame(new_published + res_published)
    return ParsedOmbudsman(by_category, by_business, published, issues)
