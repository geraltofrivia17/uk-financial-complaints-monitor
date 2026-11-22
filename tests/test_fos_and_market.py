import pandas as pd
import pytest

from complaints_monitor import fca_aggregate, fos


def _ombudsman(workbook):
    new = [
        ["Business Name", "Business Group", "Total New Cases", "New Cases", None, None],
        [None, None, None, "Banking & Credit", "Mortgages & Home Finance", "PPI"],
        ["Alpha Bank Plc", "ALPHA", 120, 100, 20, None],
        ["Gamma Finance Ltd", "No Group", 40, None, None, 40],
        ["Totals of the above", None, 160, 100, 20, 40],
        ["Overall total number of complaints", None, 200, 120, 30, 50],
    ]
    resolved = [
        [
            "Business Name",
            "Business Group",
            "Total Proactive Settlements",
            "Total % of cases upheld",
            "Closed Cases - % of cases upheld",
            None,
            None,
        ],
        [None, None, None, None, "Banking & Credit", "Mortgages & Home Finance", "PPI"],
        [None, None, None, "Average % of cases upheld", "Average %", "Average %", "Average %"],
        ["Alpha Bank Plc", "ALPHA", 5, 0.3, 0.25, 0.5, " "],
        ["Gamma Finance Ltd", "No Group", "-", 0.6, None, None, 0.6],
        ["*Figures inclusive of firms with fewer cases", None, None, None, None, None, None],
    ]
    return workbook("fos.xlsx", {"New cases": new, "Resolved cases": resolved})


def test_ombudsman_categories_and_rates(workbook):
    parsed = fos.parse_workbook(_ombudsman(workbook), "2025 H1")
    rows = parsed.by_category.set_index(["business_name", "category"])
    assert rows.loc[("Alpha Bank Plc", "Banking & credit")].new_cases == 100
    assert rows.loc[("Alpha Bank Plc", "Mortgages & home finance")].pct_upheld == 0.5
    assert rows.loc[("Gamma Finance Ltd", "PPI")].product_group == "Insurance & pure protection"
    assert set(parsed.by_category.business_name) == {"Alpha Bank Plc", "Gamma Finance Ltd"}


def test_ombudsman_totals_rows_are_kept_apart_for_reconciliation(workbook):
    parsed = fos.parse_workbook(_ombudsman(workbook), "2025 H1")
    totals = parsed.published_totals
    listed = totals[(totals.row == "Totals of the above") & (totals.category == "total")].value.item()
    assert listed == parsed.by_category.new_cases.sum() == 160
    business = parsed.by_business.set_index("business_name")
    assert business.loc["Alpha Bank Plc"].proactive_settlements == 5
    assert pd.isna(business.loc["Gamma Finance Ltd"].proactive_settlements)


def _vintage(label, opened_2026h1):
    rows = []
    for period, opened in (("2025 H2", 100), ("2026 H1", opened_2026h1)):
        rows.append(
            {
                "vintage": label,
                "period_label": period,
                "product_group": "Home finance",
                "measure": "opened",
                "value": opened,
            }
        )
    return pd.DataFrame(rows)


def test_latest_release_wins_and_revisions_are_recorded():
    vintages = pd.concat([_vintage("2026 H1", 50), _vintage("2026 H2", 55)])
    latest = fca_aggregate.latest_vintage(vintages).set_index("period_label")
    assert latest.loc["2026 H1", "opened"] == 55
    revisions = fca_aggregate.revisions(vintages)
    assert revisions.change.tolist() == [5]
    assert revisions.change_pct.iloc[0] == pytest.approx(0.1)


@pytest.mark.parametrize(
    "variable_type, variable, measure",
    [
        ("Complaints closed", "Complaints closed > 3 days but within 8 weeks", "closed_3d_8w"),
        ("Complaints closed", "Complaints closed > 8 weeks", "closed_over_8w"),
        ("Complaints closed", "Complaints closed within 3 days", "closed_within_3d"),
        ("Redress paid", "Total redress paid for complaints not upheld", "redress_not_upheld"),
        ("Redress paid", "Total redress paid for upheld complaints", "redress_upheld"),
        ("Provision", "Provision (at reporting period end date)", None),
    ],
)
def test_market_variables(variable_type, variable, measure):
    assert fca_aggregate.classify_variable(variable_type, variable) == measure


@pytest.mark.parametrize(
    "header, kind",
    [
        ("Closed Cases - % of cases upheld Average % of cases upheld", "total"),
        ("Total % Change Average % of cases upheld", "total"),
        ("Total* Proactively resolved cases", "proactive_settlements"),
        ("Plevin total = 1,208", None),
        ("Product Type Name Not Recorded", "Product type not recorded"),
        ("Closed Cases - % of cases upheld Banking & Credit Average % of cases upheld", "Banking & credit"),
    ],
)
def test_ombudsman_headers(header, kind):
    assert fos.classify_column(header) == kind
