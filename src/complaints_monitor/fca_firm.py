"""Parser for the FCA's firm-level complaints workbooks, 2016 H2 onwards.

The workbook layout changed several times: sheets were renamed and reordered,
header rows moved, columns were added and removed, and one sheet's columns are
shifted against its header. Nothing here relies on a sheet's position or exact
name. Sheets are recognised by what their title says, columns by their header
text, and each row's reporting period by finding the cell that holds one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .names import clean_name, is_total_row, name_key
from .periods import Period, normalise_label, parse_period
from .workbook import cell, is_blank, is_error_token, read_sheets, to_number

PRODUCT_GROUPS = [
    "Banking and credit cards",
    "Decumulation & pensions",
    "Home finance",
    "Insurance & pure protection",
    "Investments",
]
_PRODUCT_PREFIXES = {
    "banking": PRODUCT_GROUPS[0],
    "decumulation": PRODUCT_GROUPS[1],
    "home finance": PRODUCT_GROUPS[2],
    "insurance": PRODUCT_GROUPS[3],
    "investments": PRODUCT_GROUPS[4],
}
COUNT_METRICS = ("opened", "closed")
RATE_METRICS = ("pct_closed_3d", "pct_closed_3d_8w", "pct_closed_within_8w", "pct_upheld")
CONTEXT_METRICS = ("context_provision", "context_intermediation")
_SEMESTER = re.compile(r"^\s*\d{4}\s*H[12]\s*$", re.IGNORECASE)


def classify_sheet(title: str) -> str | None:
    """Which table a sheet holds, judged from its title. None for sheets not used."""
    text = re.sub(r"^\s*\(?\d+(\.\d+)?\)?", "", title).lower()
    text = re.sub(r"\s+", " ", text).strip()
    if "trading" in text:
        return "trading_names"
    if any(word in text for word in ("joint", "notes", "contents", "web")):
        return None
    if "context" in text:
        return "context_provision" if "prov" in text else "context_intermediation"
    if "consumer credit" in text:
        return "consumer_credit"
    if "upheld" in text:
        return "pct_upheld"
    if "3 day" in text:
        later = "8" in text or "after" in text or ">3" in text or "> 3" in text
        return "pct_closed_3d_8w" if later else "pct_closed_3d"
    if "8 week" in text:
        return "pct_closed_within_8w"
    if text.startswith("open"):
        return "opened"
    if text == "closed":
        return "closed"
    return None


@dataclass
class Layout:
    header_row: int
    firm: int
    period: int
    group: int | None = None
    joint: int | None = None
    products: dict[str, int] = field(default_factory=dict)


def find_layout(rows: list[tuple]) -> Layout | None:
    """Locate the header row and the columns that matter."""
    for index, row in enumerate(rows[:40]):
        texts = [re.sub(r"\s+", " ", str(c)).strip().lower() if c is not None else "" for c in row]
        firm = next((i for i, t in enumerate(texts) if re.fullmatch(r"firms? name|row labels", t)), None)
        period = next(
            (i for i, t in enumerate(texts) if re.match(r"reporting period|submission date", t)), None
        )
        if firm is None or period is None:
            continue
        layout = Layout(header_row=index, firm=firm, period=period)
        layout.group = next((i for i, t in enumerate(texts) if "group" in t and "product" not in t), None)
        layout.joint = next((i for i, t in enumerate(texts) if t.startswith("joint")), None)
        for i, text in enumerate(texts):
            for prefix, product in _PRODUCT_PREFIXES.items():
                if text.startswith(prefix) and product not in layout.products:
                    layout.products[product] = i
        return layout
    return None


def locate_period(row: tuple, expected: int) -> tuple[Period | None, int]:
    """The row's reporting period and how far it sits from the header's period column."""
    period = parse_period(cell(row, expected))
    if period is not None:
        return period, 0
    for index, value in enumerate(row):
        period = parse_period(value)
        if period is not None:
            return period, index - expected
    return None, 0


@dataclass
class ParsedWorkbook:
    firm_product: pd.DataFrame
    consumer_credit: pd.DataFrame
    firm_reference: pd.DataFrame
    issues: list[dict]


def _issue(issues: list[dict], label: str, sheet: str, firm: str, kind: str, detail: str = "") -> None:
    issues.append(
        {
            "source": "FCA firm",
            "period_label": label,
            "sheet": sheet.strip(),
            "firm_name": firm,
            "issue": kind,
            "detail": detail,
        }
    )


def _read_metric_sheet(rows, metric, label, sheet, issues) -> list[dict]:
    layout = find_layout(rows)
    if layout is None or not layout.products:
        _issue(issues, label, sheet, "", "sheet layout not recognised")
        return []
    records = []
    for row in rows[layout.header_row + 1 :]:
        raw_name = cell(row, layout.firm)
        if is_blank(raw_name) or is_total_row(raw_name):
            continue
        firm = clean_name(raw_name)
        period, offset = locate_period(row, layout.period)
        values = {p: cell(row, col + offset) for p, col in layout.products.items()}
        if period is None:
            if any(to_number(v) is not None for v in values.values()):
                _issue(issues, label, sheet, firm, "row has figures but no reporting period")
            continue
        if offset:
            _issue(issues, label, sheet, firm, "columns shifted against header", f"offset {offset}")
        group = cell(row, layout.group) if layout.group is not None else None
        joint = cell(row, layout.joint) if layout.joint is not None else None
        for product, raw in values.items():
            value = to_number(raw)
            if value is None:
                if not is_blank(raw) and not is_error_token(raw):
                    _issue(issues, label, sheet, firm, "non-numeric value ignored", f"{product}: {raw!r}")
                elif is_error_token(raw):
                    _issue(issues, label, sheet, firm, "spreadsheet error value ignored", f"{product}: {raw}")
                continue
            if metric in RATE_METRICS and not 0 <= value <= 1:
                _issue(issues, label, sheet, firm, "percentage outside 0 to 100%", f"{product}: {value}")
                continue
            if value < 0:
                _issue(issues, label, sheet, firm, "negative value ignored", f"{product}: {value}")
                continue
            records.append(
                {
                    "period_label": label,
                    "firm_name": firm,
                    "firm_group": clean_name(group) if not is_blank(group) else None,
                    "joint_report": str(joint).strip().lower() in {"yes", "y", "true"}
                    if not is_blank(joint)
                    else None,
                    "reporting_start": period.start,
                    "reporting_end": period.end,
                    "product_group": product,
                    "metric": metric,
                    "value": value,
                }
            )
    return records


def _read_consumer_credit(rows, label, sheet, issues) -> list[dict]:
    layout = find_layout(rows)
    if layout is None:
        _issue(issues, label, sheet, "", "sheet layout not recognised")
        return []
    records = []
    for row in rows[layout.header_row + 1 :]:
        raw_name = cell(row, layout.firm)
        if is_blank(raw_name) or is_total_row(raw_name):
            continue
        firm = clean_name(raw_name)
        period, offset = locate_period(row, layout.period)
        if period is None:
            continue
        if offset:
            _issue(issues, label, sheet, firm, "columns shifted against header", f"offset {offset}")
        period_index = layout.period + offset
        figures: list[float | None] = []
        for value in row[period_index + 1 :]:
            if isinstance(value, str):
                text = value.strip()
                if _SEMESTER.match(text):
                    continue
                if text and to_number(text) is None and not is_error_token(text):
                    continue  # a web address or other note
            figures.append(to_number(value))
            if len(figures) == 3:
                break
        figures += [None] * (3 - len(figures))
        frequency = next(
            (
                str(v).strip()
                for v in row
                if isinstance(v, str)
                and re.fullmatch(r"\s*(half[- ]?yearly|year|yearly|annual|annually)\s*", v, re.IGNORECASE)
            ),
            None,
        )
        group = cell(row, layout.group) if layout.group is not None else None
        joint = cell(row, layout.joint) if layout.joint is not None else None
        if not is_blank(joint) and str(joint).strip().lower() not in {"yes", "no", "y", "n"}:
            joint = None
        records.append(
            {
                "period_label": label,
                "firm_name": firm,
                "firm_group": clean_name(group) if not is_blank(group) else None,
                "joint_report": str(joint).strip().lower() in {"yes", "y", "true"}
                if not is_blank(joint)
                else None,
                "frequency": frequency,
                "reporting_start": period.start,
                "reporting_end": period.end,
                "received": figures[0],
                "closed": figures[1],
                "upheld_as_published": figures[2],
            }
        )
    return records


def _resolve_upheld(frame: pd.DataFrame, label: str, issues: list[dict]) -> pd.DataFrame:
    """The upheld column is a share in most releases and a count in a few. Make it a share."""
    if frame.empty:
        frame["pct_upheld"] = pd.Series(dtype=float)
        frame["upheld_unit"] = pd.Series(dtype=str)
        return frame
    values = frame["upheld_as_published"].dropna()
    values = values[values != 0]
    is_share = len(values) == 0 or ((values > 0) & (values < 1)).mean() >= 0.5
    frame["upheld_unit"] = "share" if is_share else "count"
    if is_share:
        frame["pct_upheld"] = frame["upheld_as_published"]
    else:
        closed = frame["closed"].where(frame["closed"] > 0)
        frame["pct_upheld"] = frame["upheld_as_published"] / closed
    bad = frame["pct_upheld"].notna() & ~frame["pct_upheld"].between(0, 1)
    for row in frame[bad].itertuples():
        _issue(
            issues,
            label,
            "Consumer credit",
            row.firm_name,
            "percentage outside 0 to 100%",
            f"upheld {row.upheld_as_published} of {row.closed} closed",
        )
    frame.loc[bad, "pct_upheld"] = None
    return frame


def _read_firm_reference(rows, label) -> list[dict]:
    """FCA firm reference numbers, where a release's trading-names sheet gives them."""
    for index, row in enumerate(rows[:12]):
        texts = [str(c).strip().lower() if c is not None else "" for c in row]
        if "frn" in texts and any(t.startswith("firm name") for t in texts):
            name_col = next(i for i, t in enumerate(texts) if t.startswith("firm name"))
            frn_col = texts.index("frn")
            out = []
            for data in rows[index + 1 :]:
                frn = to_number(cell(data, frn_col))
                if is_blank(cell(data, name_col)) or frn is None:
                    continue
                out.append(
                    {"period_label": label, "firm_name": clean_name(cell(data, name_col)), "frn": int(frn)}
                )
            return out
    return []


_KEYS = ["period_label", "firm_key", "reporting_start", "reporting_end", "product_group"]


def parse_workbook(path: Path, period_label: str) -> ParsedWorkbook:
    label = normalise_label(period_label)
    issues: list[dict] = []
    long_records: list[dict] = []
    credit_records: list[dict] = []
    reference: list[dict] = []
    seen: set[str] = set()
    for title, rows in read_sheets(Path(path)).items():
        kind = classify_sheet(title)
        if kind is None:
            continue
        if kind == "trading_names":
            reference.extend(_read_firm_reference(rows, label))
        elif kind == "consumer_credit":
            credit_records.extend(_read_consumer_credit(rows, label, title, issues))
        else:
            if kind in seen:
                _issue(issues, label, title, "", "second sheet for the same measure ignored", kind)
                continue
            seen.add(kind)
            long_records.extend(_read_metric_sheet(rows, kind, label, title, issues))
    for required in ("opened", "closed", "pct_upheld"):
        if required not in seen:
            _issue(issues, label, "", "", "expected sheet missing", required)

    long = pd.DataFrame(long_records)
    if long.empty:
        firm_product = pd.DataFrame()
    else:
        long["firm_key"] = long["firm_name"].map(name_key)
        duplicates = long.duplicated(_KEYS + ["metric"], keep="first")
        for row in long[duplicates].itertuples():
            _issue(issues, label, row.metric, row.firm_name, "duplicate row ignored", row.product_group)
        long = long[~duplicates]
        wide = long.pivot(index=_KEYS, columns="metric", values="value").reset_index()
        wide.columns.name = None
        attributes = (
            long.sort_values("metric", key=lambda s: s.map(lambda m: 0 if m == "opened" else 1))
            .groupby(_KEYS[:4], as_index=False)[["firm_name", "firm_group", "joint_report"]]
            .first()
        )
        firm_product = wide.merge(attributes, on=_KEYS[:4], how="left")
        for metric in (*COUNT_METRICS, *RATE_METRICS, *CONTEXT_METRICS):
            if metric not in firm_product:
                firm_product[metric] = None
        active = firm_product[["opened", "closed"]].fillna(0).sum(axis=1) > 0
        dropped = int((~active).sum())
        if dropped:
            _issue(issues, label, "", "", "rows with no complaints opened or closed dropped", str(dropped))
        firm_product = firm_product[active].copy()
        no_closed = firm_product["closed"].fillna(0) == 0
        for metric in RATE_METRICS:
            firm_product.loc[no_closed, metric] = None
        firm_product["source_file"] = Path(path).name

    credit = _resolve_upheld(pd.DataFrame(credit_records), label, issues)
    if not credit.empty:
        credit["firm_key"] = credit["firm_name"].map(name_key)
        duplicates = credit.duplicated(["firm_key", "reporting_start", "reporting_end"], keep="first")
        for row in credit[duplicates].itertuples():
            _issue(issues, label, "Consumer credit", row.firm_name, "duplicate row ignored")
        credit = credit[~duplicates].copy()
        credit["source_file"] = Path(path).name

    return ParsedWorkbook(firm_product, credit, pd.DataFrame(reference), issues)
