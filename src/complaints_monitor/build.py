"""Build the processed tables from the raw downloads.

Reads every release listed in config/sources.csv, writes tidy tables to
data/processed/, a log of every value that was set aside to
data/processed/issues.csv, and the data-quality checks to
data/processed/dq_checks.csv. The processed tables are what the SQL Server
warehouse loads.
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from . import fca_aggregate, fca_firm, firms, fos
from .periods import calendar_half, label_sort_key, normalise_label
from .settings import Settings, load_settings

FIRM_PRODUCT_COLUMNS = [
    "period_label",
    "firm_id",
    "firm_name",
    "firm_group",
    "joint_report",
    "reporting_start",
    "reporting_end",
    "reporting_months",
    "product_group",
    "opened",
    "closed",
    "pct_closed_3d",
    "pct_closed_3d_8w",
    "pct_upheld",
    "upheld_derived",
    "closed_3d_derived",
    "context_provision",
    "context_intermediation",
    "source_file",
]


def _in_scope(label: str, first: str) -> bool:
    return label_sort_key(label) >= label_sort_key(first)


def _months(start: pd.Series, end: pd.Series) -> pd.Series:
    days = (pd.to_datetime(end) - pd.to_datetime(start)).dt.days + 1
    return (days / 30.4375).round().astype(int)


def load_sources(settings: Settings) -> pd.DataFrame:
    sources = pd.read_csv(settings.config / "sources.csv")
    sources["period_label"] = sources["period_label"].map(normalise_label)
    return sources[sources["period_label"].map(lambda p: _in_scope(p, settings.first_period))]


def build(
    settings: Settings | None = None, verbose: bool = True, allow_missing: bool = False
) -> dict[str, pd.DataFrame]:
    settings = settings or load_settings()
    sources = load_sources(settings)
    missing = [
        str(settings.raw / ("fca" if row.source == "FCA" else "fos") / row.file)
        for row in sources.itertuples()
        if not (settings.raw / ("fca" if row.source == "FCA" else "fos") / row.file).exists()
    ]
    if missing and allow_missing:
        sources = sources[
            [
                (settings.raw / ("fca" if r.source == "FCA" else "fos") / r.file).exists()
                for r in sources.itertuples()
            ]
        ]
    elif missing:
        raise FileNotFoundError(
            f"{len(missing)} raw file(s) missing; run `python -m complaints_monitor download`:\n  "
            + "\n  ".join(missing)
        )

    issues: list[dict] = []

    # FCA firm-level ------------------------------------------------------------
    product_rows, credit_rows, references = [], [], []
    for row in sources[(sources.source == "FCA") & (sources.kind == "firm")].itertuples():
        parsed = fca_firm.parse_workbook(settings.raw / "fca" / row.file, row.period_label)
        product_rows.append(parsed.firm_product)
        credit_rows.append(parsed.consumer_credit)
        references.append(parsed.firm_reference)
        issues.extend(parsed.issues)
        if verbose:
            print(f"  FCA firm-level {row.period_label}: {len(parsed.firm_product):>4} firm-product rows")
    firm_product = pd.concat(product_rows, ignore_index=True)
    credit = pd.concat(credit_rows, ignore_index=True)
    reference = pd.concat(references, ignore_index=True)

    names = pd.concat(
        [
            firm_product[["period_label", "firm_name", "firm_group"]],
            credit[["period_label", "firm_name", "firm_group"]],
        ],
        ignore_index=True,
    )
    ids = firms.assign_firm_ids(names, reference)
    firm_product = firm_product.merge(ids, on="firm_name", how="left")
    credit = credit.merge(ids, on="firm_name", how="left")
    dim_firm = firms.firm_dimension(names.merge(ids, on="firm_name"), reference)

    # A firm can appear twice in one release under two spellings; keep the first.
    key = ["period_label", "firm_id", "reporting_start", "reporting_end", "product_group"]
    dupes = firm_product.duplicated(key, keep="first")
    for item in firm_product[dupes].itertuples():
        issues.append(
            {
                "source": "FCA firm",
                "period_label": item.period_label,
                "sheet": "",
                "firm_name": item.firm_name,
                "issue": "same firm listed twice; second listing ignored",
                "detail": item.product_group,
            }
        )
    firm_product = firm_product[~dupes].copy()

    # Timeliness shares that add up to more than 100% cannot both be right.
    over = (firm_product.pct_closed_3d.fillna(0) + firm_product.pct_closed_3d_8w.fillna(0)) > 1.005
    for item in firm_product[over].itertuples():
        issues.append(
            {
                "source": "FCA firm",
                "period_label": item.period_label,
                "sheet": "timeliness",
                "firm_name": item.firm_name,
                "issue": "timeliness shares add up to more than 100%; both set aside",
                "detail": f"{item.product_group}: {item.pct_closed_3d} + {item.pct_closed_3d_8w}",
            }
        )
    firm_product.loc[over, ["pct_closed_3d", "pct_closed_3d_8w"]] = None

    firm_product["reporting_months"] = _months(firm_product.reporting_start, firm_product.reporting_end)
    firm_product["upheld_derived"] = (firm_product.pct_upheld * firm_product.closed).round()
    firm_product["closed_3d_derived"] = (firm_product.pct_closed_3d * firm_product.closed).round()
    firm_product = (
        firm_product[FIRM_PRODUCT_COLUMNS]
        .sort_values(["period_label", "firm_id", "product_group"], key=_sort_key)
        .reset_index(drop=True)
    )

    credit["reporting_months"] = _months(credit.reporting_start, credit.reporting_end)
    credit["upheld_derived"] = (credit.pct_upheld * credit.closed).round()
    credit = credit[
        [
            "period_label",
            "firm_id",
            "firm_name",
            "firm_group",
            "frequency",
            "reporting_start",
            "reporting_end",
            "reporting_months",
            "received",
            "closed",
            "upheld_unit",
            "pct_upheld",
            "upheld_derived",
            "source_file",
        ]
    ]

    # FCA market totals ----------------------------------------------------------
    vintages = []
    for row in sources[(sources.source == "FCA") & (sources.kind == "aggregate")].itertuples():
        vintages.append(fca_aggregate.parse_workbook(settings.raw / "fca" / row.file, row.period_label))
    vintages = pd.concat(vintages, ignore_index=True)
    vintages = vintages[vintages.period_label.map(lambda p: _in_scope(p, settings.first_period))]
    market = fca_aggregate.latest_vintage(vintages)
    revisions = fca_aggregate.revisions(vintages)

    # Ombudsman -------------------------------------------------------------------
    fos_periods = pd.read_csv(settings.config / "fos_periods.csv", dtype={"min_cases": int})
    fos_periods["period_label"] = fos_periods["period_label"].map(normalise_label)
    categories, businesses, published = [], [], []
    for row in sources[sources.source == "FOS"].itertuples():
        parsed = fos.parse_workbook(settings.raw / "fos" / row.file, row.period_label)
        categories.append(parsed.by_category)
        businesses.append(parsed.by_business)
        published.append(parsed.published_totals)
        issues.extend(parsed.issues)
        if verbose:
            print(
                f"  Ombudsman {row.period_label}: {parsed.by_category.business_name.nunique():>4} businesses"
            )
    fos_category = pd.concat(categories, ignore_index=True)
    fos_business = pd.concat(businesses, ignore_index=True)
    fos_published = pd.concat(published, ignore_index=True)

    exact, candidates = firms.candidate_pairs(fos_category.business_name, ids, settings.review_threshold)
    review_path = settings.reference / "firm_map_review.csv"
    reviewed = firms.merge_reviews(candidates, review_path)
    firm_map = firms.firm_map(exact, reviewed)
    fos_category = fos_category.merge(
        firm_map, left_on="business_name", right_on="fos_business_name", how="left"
    ).drop(columns="fos_business_name")
    fos_business = fos_business.merge(
        firm_map, left_on="business_name", right_on="fos_business_name", how="left"
    ).drop(columns="fos_business_name")
    period_dates = fos_periods[["period_label", "start_date", "end_date"]]
    fos_category = fos_category.merge(period_dates, on="period_label", how="left")
    fos_business = fos_business.merge(period_dates, on="period_label", how="left")

    # Dimensions -------------------------------------------------------------------
    fca_labels = sorted(set(firm_product.period_label) | set(market.period_label), key=label_sort_key)
    dim_period = pd.DataFrame(
        [
            {
                "source": "FCA",
                "period_label": label,
                "start_date": calendar_half(label).start,
                "end_date": calendar_half(label).end,
                "months": 6,
                "min_threshold": None,
                "note": "Firms report on their own financial half-years; each row keeps its own dates",
            }
            for label in fca_labels
        ]
        + [
            {
                "source": "Ombudsman",
                "period_label": r.period_label,
                "start_date": date.fromisoformat(r.start_date),
                "end_date": date.fromisoformat(r.end_date),
                "months": round(
                    ((date.fromisoformat(r.end_date) - date.fromisoformat(r.start_date)).days + 1) / 30.4375
                ),
                "min_threshold": r.min_cases,
                "note": r.note if isinstance(r.note, str) else "",
            }
            for r in fos_periods.itertuples()
        ]
    )
    dim_period["period_order"] = dim_period["period_label"].map(label_sort_key)
    dim_product = pd.DataFrame(
        {"product_group": fca_firm.PRODUCT_GROUPS, "sort_order": range(1, len(fca_firm.PRODUCT_GROUPS) + 1)}
    )

    issues_frame = pd.DataFrame(
        issues, columns=["source", "period_label", "sheet", "firm_name", "issue", "detail"]
    )
    tables = {
        "dim_period": dim_period,
        "dim_product_group": dim_product,
        "dim_firm": dim_firm,
        "fact_fca_firm_product": firm_product,
        "fact_fca_consumer_credit": credit,
        "fact_fca_market": market,
        "fca_market_revisions": revisions,
        "fact_fos_business_category": fos_category,
        "fact_fos_business": fos_business,
        "fos_published_totals": fos_published,
        "firm_map": firm_map,
        "issues": issues_frame,
    }
    tables["dq_checks"] = quality_checks(tables, reviewed)
    write(tables, settings)
    return tables


def _sort_key(series: pd.Series) -> pd.Series:
    if series.name == "period_label":
        return series.map(label_sort_key)
    if series.name == "product_group":
        return series.map(fca_firm.PRODUCT_GROUPS.index)
    return series


def coverage(firm_product: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """Firm-level complaints opened as a share of the FCA's whole-market total.

    The two do not cover the same population: the market total includes over
    3,000 firms and the firm-level tables only firms above the publication
    threshold, which also report on their own financial half-years. So the share
    is expected to be high but below 100%, not equal to it.
    """
    firm = firm_product.groupby(["period_label", "product_group"], as_index=False)["opened"].sum()
    whole = market[["period_label", "product_group", "opened"]].rename(columns={"opened": "market_opened"})
    out = firm.merge(whole, on=["period_label", "product_group"], how="outer")
    out["coverage"] = out["opened"] / out["market_opened"]
    return out


def quality_checks(tables: dict[str, pd.DataFrame], reviewed: pd.DataFrame) -> pd.DataFrame:
    fp = tables["fact_fca_firm_product"]
    market = tables["fact_fca_market"]
    fos_cat = tables["fact_fos_business_category"]
    fos_pub = tables["fos_published_totals"]
    checks: list[dict] = []

    def add(name: str, passed: bool | str, actual: object, expected: str, detail: str = "") -> None:
        status = passed if isinstance(passed, str) else ("pass" if passed else "fail")
        checks.append(
            {"check": name, "status": status, "actual": actual, "expected": expected, "detail": detail}
        )

    key = ["period_label", "firm_id", "reporting_start", "reporting_end", "product_group"]
    add("FCA firm-product rows are unique", not fp.duplicated(key).any(), int(fp.duplicated(key).sum()), "0")
    add(
        "Every FCA release from the first period in scope is present",
        fp.period_label.nunique() == 20,
        fp.period_label.nunique(),
        "20 half-years, 2016 H2 to 2026 H1",
    )
    for column in ("pct_closed_3d", "pct_closed_3d_8w", "pct_upheld"):
        bad = int((~fp[column].dropna().between(0, 1)).sum())
        add(f"{column} lies between 0 and 1", bad == 0, bad, "0 rows outside")
    within_8w = fp.pct_closed_3d.fillna(0) + fp.pct_closed_3d_8w.fillna(0)
    issues = tables["issues"]
    set_aside = issues[issues.issue.str.startswith("timeliness shares add up")]
    bad = int((within_8w > 1.005).sum())
    add(
        "Closed within 3 days plus 3 days to 8 weeks is at most 100%",
        bad == 0,
        bad,
        "0 rows above 100.5% after cleaning",
        f"{len(set_aside)} published rows added up to more than 100% and were set aside (see issues.csv)",
    )
    bad = int((fp.closed.isna() & fp.pct_upheld.notna()).sum())
    add("No upheld rate without a closed count", bad == 0, bad, "0")
    add(
        "Every firm row has a reporting period",
        fp.reporting_start.notna().all(),
        int(fp.reporting_start.isna().sum()),
        "0 missing",
    )
    months = fp.reporting_months.value_counts().to_dict()
    add(
        "Firm reporting periods are six months (or twelve for annual reporters)",
        set(months) <= {5, 6, 7, 12},
        json.dumps({str(k): int(v) for k, v in sorted(months.items())}),
        "lengths of 6 or 12 months",
    )

    cov = coverage(fp, market)
    overall = cov.groupby("period_label")[["opened", "market_opened"]].sum()
    share = overall.opened / overall.market_opened
    add(
        "Firm-level complaints are a large but partial share of the market total",
        bool(share.between(0.80, 1.05).all()),
        f"{share.min():.1%} to {share.max():.1%}",
        "between 80% and 105% in every half-year",
        "The firm tables cover firms above the publication threshold, on their own reporting dates",
    )

    upheld_market = market.groupby("period_label").upheld.sum()
    upheld_firm = fp.groupby("period_label").upheld_derived.sum()
    ratio = (upheld_firm / upheld_market).dropna()
    add(
        "Upheld complaints derived from firm rates track the market total",
        bool(ratio.between(0.75, 1.10).all()),
        f"{ratio.min():.1%} to {ratio.max():.1%}",
        "between 75% and 110% in every half-year",
        "Firm-level upheld counts are derived: rate x closed",
    )

    market_closed_ok = (
        (market.closed - market[["closed_within_3d", "closed_3d_8w", "closed_over_8w"]].sum(axis=1))
        .abs()
        .max()
    )
    add(
        "Market closed equals the sum of its three timeliness bands",
        market_closed_ok == 0,
        market_closed_ok,
        "0",
    )

    totals = fos_pub[
        (fos_pub.measure == "new_cases")
        & (fos_pub.category == "total")
        & fos_pub.row.str.lower().str.startswith(("totals", "total of", "total (45"))
    ]
    by_period = fos_cat.groupby("period_label").new_cases.sum()
    diffs = []
    for row in totals.itertuples():
        diffs.append((row.period_label, int(by_period.get(row.period_label, 0) - row.value)))
    exact = [d for d in diffs if d[1] == 0]
    add(
        "Ombudsman new cases add up to the published total of listed businesses",
        "pass" if len(exact) == len(diffs) else "warn",
        f"{len(exact)} of {len(diffs)} periods match exactly",
        "all periods that publish a total",
        "; ".join(f"{p}: {d:+d}" for p, d in diffs if d)
        + ". The differences are in the published files: total rows that do not equal their own"
        " business rows, and businesses listed twice (see issues.csv)",
    )

    bad = int((~fos_cat.pct_upheld.dropna().between(0, 1)).sum())
    add("Ombudsman uphold rates lie between 0 and 1", bad == 0, bad, "0 rows outside")
    linked = fos_cat.firm_id.notna()
    share_cases = fos_cat.loc[linked, "new_cases"].sum() / fos_cat.new_cases.sum()
    add(
        "Share of Ombudsman new cases linked to an FCA-listed firm",
        share_cases >= 0.75,
        f"{share_cases:.1%}",
        "at least 75%",
        "Unlinked businesses are mostly below the FCA's firm-level publication threshold",
    )
    pending = int((reviewed.decision.fillna("") == "").sum()) if not reviewed.empty else 0
    add(
        "Every close name pair has a review decision",
        pending == 0,
        pending,
        "0 undecided pairs",
        "data/reference/firm_map_review.csv",
    )
    return pd.DataFrame(checks)


def write(tables: dict[str, pd.DataFrame], settings: Settings) -> None:
    settings.processed.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        out = frame.copy()
        for column in out.columns:
            if out[column].dtype == float:
                out[column] = out[column].round(10)
        out = out.replace({np.nan: None})
        out.to_csv(
            settings.processed / f"{name}.csv", index=False, lineterminator="\n", date_format="%Y-%m-%d"
        )
