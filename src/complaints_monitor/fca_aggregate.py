"""Parser for the FCA's aggregate (whole-market) complaints workbooks.

From 2022 H1 each release is a tidy table covering about ten half-years, so
releases overlap and later ones restate earlier figures. Every release is read
as a "vintage"; the latest vintage of each figure is used, and the differences
between vintages are kept as a record of revisions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .fca_firm import PRODUCT_GROUPS
from .periods import label_sort_key, normalise_label
from .workbook import read_sheets, to_number

MEASURES = [
    "opened",
    "closed_within_3d",
    "closed_3d_8w",
    "closed_over_8w",
    "upheld",
    "redress_upheld",
    "redress_not_upheld",
]


def classify_variable(variable_type: str, variable: str) -> str | None:
    kind = variable_type.strip().lower()
    text = re.sub(r"\s+", " ", variable).strip().lower()
    if kind == "complaints opened":
        return "opened"
    if kind == "complaints closed":
        if "> 8 weeks" in text or ">8 weeks" in text:
            return "closed_over_8w"
        if "> 3 days" in text or ">3 days" in text:
            return "closed_3d_8w"
        if "within 3 days" in text:
            return "closed_within_3d"
        return None
    if kind == "complaints upheld":
        return "upheld"
    if kind == "redress paid":
        return "redress_not_upheld" if "not upheld" in text else "redress_upheld"
    return None


def _product_group(name: str) -> str | None:
    lowered = name.strip().lower()
    for group in PRODUCT_GROUPS:
        if lowered == group.lower():
            return group
    return None


def parse_workbook(path: Path, vintage_label: str) -> pd.DataFrame:
    """Rows of (vintage, period_label, product_group, measure, value) from one release."""
    vintage = normalise_label(vintage_label)
    sheets = read_sheets(Path(path))
    title = next((t for t in sheets if re.fullmatch(r"product group", t.strip(), re.IGNORECASE)), None)
    if title is None:
        raise ValueError(f"{Path(path).name}: no 'Product Group' sheet")
    rows = sheets[title]
    header = [
        re.sub(r"[^a-z ]", "", str(c).lower()).replace(" group", "").strip() if c is not None else ""
        for c in rows[0]
    ]
    try:
        semester = header.index("semester")
        product = next(i for i, h in enumerate(header) if h.startswith("product"))
        variable_type = next(i for i, h in enumerate(header) if h.startswith("variable type"))
        variable = next(i for i, h in enumerate(header) if h == "variable")
        volume = header.index("volume")
    except (ValueError, StopIteration) as error:
        raise ValueError(f"{Path(path).name}: unexpected columns {rows[0]}") from error
    records = []
    for row in rows[1:]:
        if row[semester] is None or not re.match(r"^\s*\d{4}\s*H[12]\s*$", str(row[semester])):
            continue
        group = _product_group(str(row[product]))
        measure = classify_variable(str(row[variable_type]), str(row[variable]))
        value = to_number(row[volume])
        if group is None or measure is None or value is None:
            continue
        records.append(
            {
                "vintage": vintage,
                "period_label": normalise_label(str(row[semester])),
                "product_group": group,
                "measure": measure,
                "value": value,
            }
        )
    frame = pd.DataFrame(records)
    # "Complaints opened" is split by cause in some releases; add the causes up.
    return frame.groupby(["vintage", "period_label", "product_group", "measure"], as_index=False)[
        "value"
    ].sum()


def latest_vintage(all_vintages: pd.DataFrame) -> pd.DataFrame:
    """One row per period and product group, each figure taken from its latest release."""
    ordered = all_vintages.assign(_order=all_vintages["vintage"].map(label_sort_key)).sort_values("_order")
    latest = ordered.groupby(["period_label", "product_group", "measure"], as_index=False).last()
    wide = latest.pivot(
        index=["period_label", "product_group"], columns="measure", values="value"
    ).reset_index()
    wide.columns.name = None
    vintages = latest.groupby(["period_label", "product_group"], as_index=False)["vintage"].max()
    wide = wide.merge(
        vintages.rename(columns={"vintage": "latest_vintage"}), on=["period_label", "product_group"]
    )
    for measure in MEASURES:
        if measure not in wide:
            wide[measure] = None
    wide["closed"] = wide[["closed_within_3d", "closed_3d_8w", "closed_over_8w"]].sum(axis=1, min_count=3)
    return wide.sort_values(
        ["period_label", "product_group"],
        key=lambda s: s.map(
            lambda v: label_sort_key(v) if re.match(r"^\d{4} H", str(v)) else PRODUCT_GROUPS.index(v)
        ),
    )


def revisions(all_vintages: pd.DataFrame) -> pd.DataFrame:
    """Figures whose first published value differs from the latest published value."""
    ordered = all_vintages.assign(_order=all_vintages["vintage"].map(label_sort_key)).sort_values("_order")
    grouped = ordered.groupby(["period_label", "product_group", "measure"])
    summary = grouped.agg(
        first_vintage=("vintage", "first"),
        first_value=("value", "first"),
        latest_vintage=("vintage", "last"),
        latest_value=("value", "last"),
        vintages=("vintage", "nunique"),
    ).reset_index()
    summary["change"] = summary["latest_value"] - summary["first_value"]
    summary["change_pct"] = summary["change"] / summary["first_value"].where(summary["first_value"] != 0)
    return summary[summary["change"] != 0].reset_index(drop=True)
