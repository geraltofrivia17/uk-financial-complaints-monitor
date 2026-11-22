"""Findings tables and charts, computed from data/processed.

The same calculations exist as SQL views (sql/03_views.sql) for Power BI; the
optional SQL Server tests check that the two agree.
"""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .fca_firm import PRODUCT_GROUPS  # noqa: E402
from .periods import label_sort_key  # noqa: E402
from .settings import Settings, load_settings  # noqa: E402

# Reference categorical palette, slots 1-5, fixed order by product group.
COLOURS = dict(zip(PRODUCT_GROUPS, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"], strict=True))
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
Z95, Z998 = 1.96, 3.09
SOURCE_AGGREGATE = "All firms reporting to the FCA, 2016 H2 to 2026 H1. Source: FCA aggregate complaints data"


def _read(settings: Settings, name: str) -> pd.DataFrame:
    return pd.read_csv(settings.processed / f"{name}.csv")


def _order(frame: pd.DataFrame, column: str = "period_label") -> pd.DataFrame:
    return frame.assign(_o=frame[column].map(label_sort_key)).sort_values("_o").drop(columns="_o")


# --------------------------------------------------------------------- tables
def market_trend(market: pd.DataFrame) -> pd.DataFrame:
    total = market.groupby("period_label", as_index=False)[
        [
            "opened",
            "closed",
            "upheld",
            "closed_within_3d",
            "closed_over_8w",
            "redress_upheld",
            "redress_not_upheld",
        ]
    ].sum()
    total["product_group"] = "All products"
    frame = pd.concat([market[total.columns], total], ignore_index=True)
    frame["uphold_rate"] = frame.upheld / frame.closed
    frame["share_closed_3d"] = frame.closed_within_3d / frame.closed
    frame["share_closed_over_8w"] = frame.closed_over_8w / frame.closed
    frame["redress_total"] = frame.redress_upheld + frame.redress_not_upheld
    return _order(frame)


def overdispersion_tau2(z: pd.Series, se: pd.Series) -> float:
    """Between-firm variance for an additive random-effects funnel (Spiegelhalter, 2005).

    z-scores are winsorised at the 10th and 90th percentiles before the
    dispersion factor is estimated, so a few extreme firms do not set the limits
    for everyone. Returns 0 when there is no excess dispersion.
    """
    count = len(z)
    if count < 3:
        return 0.0
    low, high = z.quantile(0.10), z.quantile(0.90)
    phi = (z.clip(low, high) ** 2).mean()
    if count * phi <= count - 1:
        return 0.0
    weights = 1 / se**2
    return float((count * phi - (count - 1)) / (weights.sum() - (weights**2).sum() / weights.sum()))


def funnel(firm_product: pd.DataFrame, min_closed: int) -> pd.DataFrame:
    """Each firm's uphold rate against its product group's pooled rate, per release.

    Two sets of limits are given. The binomial limits assume every firm has the
    same underlying rate and differs only by chance; with hundreds of thousands of
    complaints they are very narrow, and most firms fall outside them. The
    adjusted limits add the between-firm variance (tau squared), so a firm
    outside them stands out even allowing for normal variation between firms.
    """
    rows = firm_product[(firm_product.closed >= min_closed) & firm_product.upheld_derived.notna()]
    firm = rows.groupby(["period_label", "firm_id", "product_group"], as_index=False)[
        ["closed", "upheld_derived"]
    ].sum()
    pooled = (
        firm.groupby(["period_label", "product_group"])
        .agg(
            pooled_upheld=("upheld_derived", "sum"),
            pooled_closed=("closed", "sum"),
            firms_in_group=("firm_id", "nunique"),
        )
        .reset_index()
    )
    pooled["pooled_rate"] = pooled.pooled_upheld / pooled.pooled_closed
    out = firm.merge(
        pooled[["period_label", "product_group", "pooled_rate", "firms_in_group"]],
        on=["period_label", "product_group"],
    )
    out["uphold_rate"] = out.upheld_derived / out.closed
    out["se"] = np.sqrt(out.pooled_rate * (1 - out.pooled_rate) / out.closed)
    out["z_score"] = (out.uphold_rate - out.pooled_rate) / out.se
    tau2 = (
        out.groupby(["period_label", "product_group"])[["z_score", "se"]]
        .apply(lambda g: overdispersion_tau2(g.z_score, g.se))
        .rename("tau2")
        .reset_index()
    )
    out = out.merge(tau2, on=["period_label", "product_group"])
    out["se_adjusted"] = np.sqrt(out.se**2 + out.tau2)
    for name, z in (("95", Z95), ("998", Z998)):
        out[f"lower_{name}"] = out.pooled_rate - z * out.se
        out[f"upper_{name}"] = out.pooled_rate + z * out.se
        out[f"lower_{name}_adj"] = out.pooled_rate - z * out.se_adjusted
        out[f"upper_{name}_adj"] = out.pooled_rate + z * out.se_adjusted
    out["z_adjusted"] = (out.uphold_rate - out.pooled_rate) / out.se_adjusted
    out["position_binomial"] = _position(out, "")
    out["position_adjusted"] = _position(out, "_adj")
    return out.drop(columns="se")


def _position(frame: pd.DataFrame, suffix: str) -> np.ndarray:
    return np.select(
        [
            frame.uphold_rate > frame[f"upper_998{suffix}"],
            frame.uphold_rate < frame[f"lower_998{suffix}"],
            frame.uphold_rate > frame[f"upper_95{suffix}"],
            frame.uphold_rate < frame[f"lower_95{suffix}"],
        ],
        ["Above 99.8% limit", "Below 99.8% limit", "Above 95% limit", "Below 95% limit"],
        "Within limits",
    )


def fca_vs_ombudsman(
    firm_product: pd.DataFrame, fos_category: pd.DataFrame, min_overlap_share: float
) -> pd.DataFrame:
    """Ombudsman uphold rates next to the firm's own rate for an overlapping FCA period."""
    fos = fos_category[
        fos_category.firm_id.notna()
        & fos_category.product_group.notna()
        & (fos_category.category != "PPI")
        & fos_category.pct_upheld.notna()
    ].copy()
    fos = fos.rename(
        columns={
            "period_label": "fos_period",
            "start_date": "fos_start",
            "end_date": "fos_end",
            "pct_upheld": "fos_uphold_rate",
            "new_cases": "fos_new_cases",
        }
    )
    fca = firm_product[firm_product.pct_upheld.notna() & (firm_product.closed > 0)].rename(
        columns={"period_label": "fca_period", "pct_upheld": "fca_uphold_rate", "closed": "fca_closed"}
    )
    pairs = fos.merge(
        fca[
            [
                "fca_period",
                "firm_id",
                "product_group",
                "reporting_start",
                "reporting_end",
                "fca_closed",
                "fca_uphold_rate",
            ]
        ],
        on=["firm_id", "product_group"],
    )
    for column in ("fos_start", "fos_end", "reporting_start", "reporting_end"):
        pairs[column] = pd.to_datetime(pairs[column])
    start = pairs[["fos_start", "reporting_start"]].max(axis=1)
    end = pairs[["fos_end", "reporting_end"]].min(axis=1)
    pairs["overlap_days"] = ((end - start).dt.days + 1).clip(lower=0)
    pairs["overlap_share"] = pairs.overlap_days / ((pairs.fos_end - pairs.fos_start).dt.days + 1)
    pairs = pairs[pairs.overlap_share >= min_overlap_share]
    pairs = pairs.sort_values(["overlap_days", "reporting_end"], ascending=False).drop_duplicates(
        ["fos_period", "firm_id", "product_group", "business_name"]
    )
    pairs["uphold_gap"] = pairs.fos_uphold_rate - pairs.fca_uphold_rate
    pairs["fos_cases_per_closed"] = pairs.fos_new_cases / pairs.fca_closed
    return _order(pairs, "fos_period").reset_index(drop=True)


def concentration(firm_product: pd.DataFrame, period: str) -> dict:
    latest = firm_product[firm_product.period_label == period]
    by_firm = latest.groupby("firm_id").opened.sum().sort_values(ascending=False)
    return {
        "period": period,
        "firms": int(len(by_firm)),
        "top10_share": float(by_firm.head(10).sum() / by_firm.sum()),
        "top10": by_firm.head(10).astype(int).to_dict(),
    }


# --------------------------------------------------------------------- charts
def _style(ax, title: str, subtitle: str = "") -> None:
    lines = subtitle.count("\n") + 1 if subtitle else 0
    ax.set_title(title, loc="left", fontsize=13, color=INK, pad=10 + 13 * lines, fontweight="bold")
    if subtitle:
        ax.text(0, 1.015, subtitle, transform=ax.transAxes, fontsize=9.5, color=MUTED, va="bottom")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _dodge(values: dict[str, float], gap: float) -> dict[str, float]:
    """Spread end-of-line label positions so no two are closer than `gap`."""
    ordered = sorted(values.items(), key=lambda item: item[1])
    placed: list[list] = []
    for name, value in ordered:
        if placed and value - placed[-1][1] < gap:
            value = placed[-1][1] + gap
        placed.append([name, value])
    # shift the block down if it was pushed too high, keeping the order
    overshoot = placed[-1][1] - max(values.values()) if placed else 0
    if overshoot > 0:
        for item in placed:
            item[1] -= overshoot / 2
    return dict((name, value) for name, value in placed)


def _end_labels(ax, ends: dict[str, float], x: float, gap: float) -> None:
    for name, y in _dodge(ends, gap).items():
        ax.text(x, y, name, color=INK, fontsize=8.5, va="center")


def _periods_axis(ax, labels: list[str]) -> None:
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels([label if label.endswith("H1") else "" for label in labels], rotation=0)
    ax.set_xticklabels([label.replace(" H1", "") if label.endswith("H1") else "" for label in labels])


def _line_chart(trend: pd.DataFrame, column: str, scale: float, gap: float, title: str, path) -> None:
    data = trend[trend.product_group != "All products"]
    labels = sorted(data.period_label.unique(), key=label_sort_key)
    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=150)
    ends: dict[str, float] = {}
    for group in PRODUCT_GROUPS:
        series = data[data.product_group == group].set_index("period_label").reindex(labels)
        ax.plot(range(len(labels)), series[column] * scale, color=COLOURS[group], linewidth=2)
        ends[group] = series[column].iloc[-1] * scale
    _end_labels(ax, ends, len(labels) - 0.6, gap)
    _style(ax, title, SOURCE_AGGREGATE)
    _periods_axis(ax, labels)
    ax.set_xlim(-0.5, len(labels) + 4.5)
    if column == "opened":
        ax.set_ylim(bottom=0)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def chart_volume(trend: pd.DataFrame, path) -> None:
    _line_chart(trend, "opened", 1e-6, 0.17, "Complaints opened by product group, millions", path)


def chart_uphold(trend: pd.DataFrame, path) -> None:
    _line_chart(trend, "uphold_rate", 100, 1.0, "Share of closed complaints upheld by firms, %", path)


def _label_points(ax, points: list[tuple[str, float, float]], gap: float, log_x: bool = False) -> None:
    """Label points to their right, nudging labels apart vertically."""
    positions = _dodge({name: y for name, _, y in points}, gap)
    for name, x, y in points:
        ax.annotate(
            name,
            (x, y),
            xytext=(x * 1.25 if log_x else x + 1.5, positions[name]),
            fontsize=7.5,
            color=INK,
            va="center",
            arrowprops={"arrowstyle": "-", "color": MUTED, "linewidth": 0.6},
        )


def chart_funnel(frame: pd.DataFrame, period: str, group: str, path) -> None:
    data = frame[(frame.period_label == period) & (frame.product_group == group)]
    pooled = data.pooled_rate.iloc[0]
    tau2 = data.tau2.iloc[0]
    n = np.geomspace(max(100, data.closed.min() * 0.8), data.closed.max() * 1.2, 300)
    se = np.sqrt(pooled * (1 - pooled) / n + tau2)
    fig, ax = plt.subplots(figsize=(10, 5.8), dpi=150)
    for z, style in ((Z95, (0, (4, 3))), (Z998, (0, (1, 2)))):
        ax.plot(n, (pooled + z * se) * 100, color=MUTED, linewidth=1, linestyle=style)
        ax.plot(n, (pooled - z * se) * 100, color=MUTED, linewidth=1, linestyle=style)
    ax.axhline(pooled * 100, color=MUTED, linewidth=1.2)
    outside = data.position_adjusted.str.contains("99.8")
    ax.scatter(
        data.closed[~outside],
        data.uphold_rate[~outside] * 100,
        s=36,
        color="#c3c2b7",
        edgecolor="white",
        linewidth=1,
        zorder=3,
        label="Within 99.8% limits",
    )
    ax.scatter(
        data.closed[outside],
        data.uphold_rate[outside] * 100,
        s=46,
        color=COLOURS[group],
        edgecolor="white",
        linewidth=1,
        zorder=4,
        label="Outside 99.8% limits",
    )
    largest = data.nlargest(4, "closed")
    _label_points(
        ax,
        [(r.firm_name_latest, r.closed, r.uphold_rate * 100) for r in largest.itertuples()],
        gap=3.2,
        log_x=True,
    )
    ax.set_xscale("log")
    ax.set_xlim(right=data.closed.max() * 12)
    ax.set_xlabel("Complaints closed in the half-year (log scale)", color=MUTED, fontsize=9)
    _style(
        ax,
        f"{group}: firm uphold rates, {period}, %",
        f"Pooled rate {pooled:.1%} (line). 95% (dashed) and 99.8% (dotted) limits, widened for normal "
        "variation between firms.\nFirms with 100+ closed complaints. Source: FCA firm-level complaints data",
    )
    ax.legend(frameon=False, fontsize=8.5, loc="lower right")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def chart_fca_vs_fos(pairs: pd.DataFrame, period: str, path) -> None:
    data = pairs[(pairs.fos_period == period) & (pairs.fos_new_cases >= 100)]
    fig, ax = plt.subplots(figsize=(9.5, 7), dpi=150)
    for group in PRODUCT_GROUPS:
        part = data[data.product_group == group]
        if part.empty:
            continue
        ax.scatter(
            part.fca_uphold_rate * 100,
            part.fos_uphold_rate * 100,
            s=np.clip(part.fos_new_cases / 10, 16, 300),
            color=COLOURS[group],
            alpha=0.85,
            edgecolor="white",
            linewidth=1,
            label=group,
        )
    ax.plot([0, 100], [0, 100], color=MUTED, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(88, 94, "Same rate", color=MUTED, fontsize=8, ha="right", va="center")
    gaps = data.nlargest(5, "uphold_gap")
    _label_points(
        ax,
        [(r.firm_name_latest, r.fca_uphold_rate * 100, r.fos_uphold_rate * 100) for r in gaps.itertuples()],
        gap=3.0,
    )
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Firm's own uphold rate (FCA data), %", color=MUTED, fontsize=9)
    ax.set_ylabel("Ombudsman uphold rate on escalated cases, %", color=MUTED, fontsize=9)
    _style(
        ax,
        f"Firm decisions against Ombudsman outcomes, {period}",
        "Each dot is a firm and product group with 100+ new Ombudsman cases; size shows cases.",
    )
    ax.legend(frameon=False, fontsize=8, loc="upper right", bbox_to_anchor=(1, 0.86))
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# --------------------------------------------------------------------- entry
def analyse(settings: Settings | None = None) -> dict:
    settings = settings or load_settings()
    tables_dir = settings.outputs / "tables"
    figures_dir = settings.outputs / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    market = _read(settings, "fact_fca_market")
    firm_product = _read(settings, "fact_fca_firm_product")
    fos_category = _read(settings, "fact_fos_business_category")
    latest = max(firm_product.period_label, key=label_sort_key)

    names = _read(settings, "dim_firm")[["firm_id", "firm_name_latest"]]
    trend = market_trend(market)
    funnel_all = funnel(firm_product, settings.min_closed).merge(names, on="firm_id", how="left")
    pairs = fca_vs_ombudsman(firm_product, fos_category, settings.min_overlap_share).merge(
        names, on="firm_id", how="left"
    )
    trend.to_csv(tables_dir / "market_trend.csv", index=False, lineterminator="\n")
    funnel_all.to_csv(tables_dir / "firm_funnel.csv", index=False, lineterminator="\n")
    pairs.to_csv(tables_dir / "fca_vs_ombudsman.csv", index=False, lineterminator="\n")

    chart_volume(trend, figures_dir / "complaints_opened.png")
    chart_uphold(trend, figures_dir / "uphold_rate.png")
    chart_funnel(funnel_all, latest, "Banking and credit cards", figures_dir / "funnel_banking.png")
    chart_funnel(funnel_all, latest, "Insurance & pure protection", figures_dir / "funnel_insurance.png")
    fos_latest = max(pairs.fos_period, key=label_sort_key)
    chart_fca_vs_fos(pairs, fos_latest, figures_dir / "fca_vs_ombudsman.png")

    summary = summarise(trend, funnel_all, pairs, firm_product, latest, fos_latest)
    (settings.outputs / "findings.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    print(
        f"  wrote {len(list(tables_dir.glob('*.csv')))} tables, "
        f"{len(list(figures_dir.glob('*.png')))} charts, "
        "findings.json"
    )
    return summary


def summarise(trend, funnel_all, pairs, firm_product, latest, fos_latest) -> dict:
    total = trend[trend.product_group == "All products"].set_index("period_label")
    peak = total.opened.idxmax()
    first = min(total.index, key=label_sort_key)
    year_ago = [p for p in total.index if label_sort_key(p) == label_sort_key(latest) - 10][0]
    latest_funnel = funnel_all[funnel_all.period_label == latest]
    outside = latest_funnel[latest_funnel.position_adjusted.str.contains("99.8")]
    binomial_outside = latest_funnel[latest_funnel.position_binomial.str.contains("99.8")]
    fos_now = pairs[pairs.fos_period == fos_latest]
    weighted = lambda d, c: float((d[c] * d.fos_new_cases).sum() / d.fos_new_cases.sum())  # noqa: E731
    return {
        "latest_fca_period": latest,
        "first_period": first,
        "opened_latest": int(total.loc[latest, "opened"]),
        "opened_year_ago": int(total.loc[year_ago, "opened"]),
        "uphold_rate_latest": float(total.loc[latest, "uphold_rate"]),
        "uphold_rate_year_ago": float(total.loc[year_ago, "uphold_rate"]),
        "share_closed_3d_latest": float(total.loc[latest, "share_closed_3d"]),
        "share_closed_over_8w_latest": float(total.loc[latest, "share_closed_over_8w"]),
        "redress_latest": int(total.loc[latest, "redress_total"]),
        "peak_period": peak,
        "opened_peak": int(total.loc[peak, "opened"]),
        "redress_peak_period": total.redress_total.idxmax(),
        "redress_peak": int(total.redress_total.max()),
        "by_product_latest": {
            row.product_group: {
                "opened": int(row.opened),
                "uphold_rate": float(row.uphold_rate),
                "share_closed_3d": float(row.share_closed_3d),
            }
            for row in trend[
                (trend.period_label == latest) & (trend.product_group != "All products")
            ].itertuples()
        },
        "concentration": concentration(firm_product, latest),
        "funnel": {
            "firm_groups_compared": int(len(latest_funnel)),
            "outside_998_binomial": int(len(binomial_outside)),
            "outside_998_adjusted": int(len(outside)),
            "above_998_adjusted": int((outside.position_adjusted == "Above 99.8% limit").sum()),
            "below_998_adjusted": int((outside.position_adjusted == "Below 99.8% limit").sum()),
            "above_998_adjusted_firms": outside[outside.position_adjusted == "Above 99.8% limit"][
                ["firm_id", "firm_name_latest", "product_group", "closed", "uphold_rate", "pooled_rate"]
            ].to_dict(orient="records"),
            "below_998_adjusted_firms": outside[outside.position_adjusted == "Below 99.8% limit"][
                ["firm_id", "firm_name_latest", "product_group", "closed", "uphold_rate", "pooled_rate"]
            ].to_dict(orient="records"),
            "by_group": latest_funnel.groupby("product_group")
            .position_adjusted.value_counts()
            .unstack(fill_value=0)
            .to_dict(orient="index"),
        },
        "fca_vs_ombudsman": {
            "fos_period": fos_latest,
            "pairs": int(len(fos_now)),
            "pairs_with_100_cases": int((fos_now.fos_new_cases >= 100).sum()),
            "fos_rate_case_weighted": weighted(fos_now, "fos_uphold_rate"),
            "fca_rate_case_weighted": weighted(fos_now, "fca_uphold_rate"),
            "share_pairs_fos_higher": float((fos_now.uphold_gap > 0).mean()),
            "correlation_fca_fos": float(fos_now[["fca_uphold_rate", "fos_uphold_rate"]].corr().iloc[0, 1]),
            "largest_gaps": fos_now[fos_now.fos_new_cases >= 100]
            .nlargest(8, "uphold_gap")[
                [
                    "firm_id",
                    "firm_name_latest",
                    "product_group",
                    "fca_uphold_rate",
                    "fos_uphold_rate",
                    "fos_new_cases",
                    "fca_period",
                    "fos_period",
                ]
            ]
            .to_dict(orient="records"),
            "periods_compared": sorted(pairs.fos_period.unique().tolist(), key=label_sort_key),
        },
    }
