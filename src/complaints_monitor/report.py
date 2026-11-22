"""Write README.md from templates/README.md.tmpl and the measured outputs.

Every number in the README comes from outputs/findings.json or
data/processed/dq_checks.csv, so the text cannot drift from the data.
"""

from __future__ import annotations

import json
import string

import pandas as pd

from .settings import Settings, load_settings


def pct(value: float, places: int = 1) -> str:
    return f"{value * 100:.{places}f}%"


def millions(value: float, places: int = 2) -> str:
    return f"{value / 1e6:.{places}f} million"


def pounds_m(value: float) -> str:
    return f"£{value / 1e6:,.0f}m"


def fields(settings: Settings) -> dict[str, str]:
    s = json.loads((settings.outputs / "findings.json").read_text())
    checks = pd.read_csv(settings.processed / "dq_checks.csv")
    issues = pd.read_csv(settings.processed / "issues.csv")
    fp = pd.read_csv(settings.processed / "fact_fca_firm_product.csv")
    fos = pd.read_csv(settings.processed / "fact_fos_business_category.csv")
    dim = pd.read_csv(settings.processed / "dim_firm.csv")
    firm_map = pd.read_csv(settings.processed / "firm_map.csv")
    review = pd.read_csv(settings.reference / "firm_map_review.csv")
    funnel = s["funnel"]
    ombudsman = s["fca_vs_ombudsman"]
    products = s["by_product_latest"]
    top = list(s["concentration"]["top10"].items())
    names = dim.set_index("firm_id").firm_name_latest
    renamed = fp.groupby("firm_id").firm_name.nunique()
    coverage = checks.set_index("check").actual
    return {
        "latest": s["latest_fca_period"],
        "first": s["first_period"],
        "opened_latest": millions(s["opened_latest"]),
        "opened_year_ago": millions(s["opened_year_ago"]),
        "opened_peak": millions(s["opened_peak"], 1),
        "peak_period": s["peak_period"],
        "uphold_latest": pct(s["uphold_rate_latest"]),
        "uphold_year_ago": pct(s["uphold_rate_year_ago"]),
        "closed_3d_latest": pct(s["share_closed_3d_latest"]),
        "closed_8w_latest": pct(s["share_closed_over_8w_latest"]),
        "redress_latest": pounds_m(s["redress_latest"]),
        "redress_peak": pounds_m(s["redress_peak"]),
        "redress_peak_period": s["redress_peak_period"],
        "pensions_uphold": pct(products["Decumulation & pensions"]["uphold_rate"]),
        "pensions_3d": pct(products["Decumulation & pensions"]["share_closed_3d"]),
        "banking_3d": pct(products["Banking and credit cards"]["share_closed_3d"]),
        "banking_opened": f"{products['Banking and credit cards']['opened']:,}",
        "insurance_opened": f"{products['Insurance & pure protection']['opened']:,}",
        "firms_latest": str(s["concentration"]["firms"]),
        "top10_share": pct(s["concentration"]["top10_share"], 0),
        "top3": ", ".join(names[f] for f, _ in top[:3]),
        "funnel_compared": str(funnel["firm_groups_compared"]),
        "funnel_binomial": str(funnel["outside_998_binomial"]),
        "funnel_adjusted": str(funnel["outside_998_adjusted"]),
        "funnel_above": str(funnel["above_998_adjusted"]),
        "funnel_below": str(funnel["below_998_adjusted"]),
        "fos_period": ombudsman["fos_period"],
        "fos_pairs": str(ombudsman["pairs"]),
        "fos_rate": pct(ombudsman["fos_rate_case_weighted"]),
        "fca_rate": pct(ombudsman["fca_rate_case_weighted"]),
        "fos_higher": pct(ombudsman["share_pairs_fos_higher"], 0),
        "fos_corr": f"{ombudsman['correlation_fca_fos']:.2f}",
        "fos_periods": str(len(ombudsman["periods_compared"])),
        "firm_rows": f"{len(fp):,}",
        "firms_total": f"{len(dim):,}",
        "firms_renamed": str(int((renamed > 1).sum())),
        "fos_rows": f"{len(fos):,}",
        "fos_link_share": str(coverage["Share of Ombudsman new cases linked to an FCA-listed firm"]),
        "fos_linked_names": f"{len(firm_map):,}",
        "review_pairs": str(len(review)),
        "review_rejected": str(int((review.decision == "reject").sum())),
        "checks_total": str(len(checks)),
        "checks_pass": str(int((checks.status == "pass").sum())),
        "checks_warn": str(int((checks.status == "warn").sum())),
        "issues_total": f"{len(issues):,}",
        "upheld_range": str(coverage["Upheld complaints derived from firm rates track the market total"]),
        "coverage_range": str(
            coverage["Firm-level complaints are a large but partial share of the market total"]
        ),
    }


def write_readme(settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    template = string.Template((settings.root / "templates" / "README.md.tmpl").read_text(encoding="utf-8"))
    text = template.substitute(fields(settings))
    (settings.root / "README.md").write_text(text, encoding="utf-8")
    print("  wrote README.md")
