import pandas as pd
import pytest

from complaints_monitor.fca_firm import classify_sheet, parse_workbook

HEADER = [
    "Firm Name",
    "Group",
    "Joint Reporting",
    "Reporting period",
    "Banking and credit cards",
    "Decumulation & pensions",
    "Home finance",
    "Insurance & pure protection",
    "Investments",
    "Grand Total",
]


@pytest.mark.parametrize(
    "title, kind",
    [
        ("(1.3) Open", "opened"),
        ("Opened", "opened"),
        ("opened", "opened"),
        ("Closed", "closed"),
        ("(1.5) Closed 3 Days", "pct_closed_3d"),
        ("% closed in 3 days", "pct_closed_3d"),
        ("Percentage within 3 days", "pct_closed_3d"),
        ("(1.6) Closed 3 days to 8 weeks", "pct_closed_3d_8w"),
        ("% >3 days & <8 ", "pct_closed_3d_8w"),
        ("after 3 days within 8 weeks", "pct_closed_3d_8w"),
        ("Percentage after 3 days, within", "pct_closed_3d_8w"),
        ("(1.7) Closed 8 weeks", "pct_closed_within_8w"),
        ("(1.8) Upheld ", "pct_upheld"),
        ("% upheld ", "pct_upheld"),
        ("Percentage upheld", "pct_upheld"),
        ("Context A Provision", "context_provision"),
        ("Context – Intermediation", "context_intermediation"),
        ("Consumer Credit", "consumer_credit"),
        ("Trading Names", "trading_names"),
        ("Main return Joint reporters", None),
        ("Notes", None),
        ("Contents", None),
    ],
)
def test_sheets_are_recognised_by_title(title, kind):
    assert classify_sheet(title) == kind


def _release(workbook, upheld_rows=None, credit_rows=None):
    opened = [
        HEADER,
        ["Alpha Bank Plc", "ALPHA GROUP", "no", "2026-01-01 to 2026-06-30", 900, None, 100, None, None, 1000],
        [
            "Beta Insurance Ltd",
            "NO GROUP",
            "no",
            "2025-10-01 to 2026-03-31",
            None,
            None,
            None,
            "750",
            None,
            750,
        ],
        ["Grand Total", None, None, None, 900, None, 100, 750, None, 1750],
    ]
    closed = [
        HEADER,
        ["Alpha Bank Plc", "ALPHA GROUP", "no", "2026-01-01 to 2026-06-30", 800, None, 0, None, None, 800],
        [
            "Beta Insurance Ltd",
            "NO GROUP",
            "no",
            "2025-10-01 to 2026-03-31",
            None,
            None,
            None,
            700,
            None,
            700,
        ],
    ]
    upheld = upheld_rows or [
        HEADER,
        [
            "Alpha Bank Plc",
            "ALPHA GROUP",
            "no",
            "2026-01-01 to 2026-06-30",
            0.5,
            None,
            "#DIV/0!",
            None,
            None,
            0.5,
        ],
        [
            "Beta Insurance Ltd",
            "NO GROUP",
            "no",
            "2025-10-01 to 2026-03-31",
            None,
            None,
            None,
            1.4,
            None,
            1.4,
        ],
    ]
    credit = credit_rows or [
        [
            "Firm Name",
            "Group",
            "Joint Reporting",
            "Reporting frequency",
            "Reporting period",
            "Semester",
            "Complaints Received",
            "Complaints Closed",
            "Complaints upheld by firm",
        ],
        [
            "Alpha Bank Plc",
            "ALPHA GROUP",
            "no",
            "Half Yearly",
            "2026-01-01 to 2026-06-30",
            "2026 H1",
            40,
            20,
            0.25,
        ],
    ]
    trading = [
        ["Firm Name", "FRN", "Other Trading Names", "Firm Group"],
        ["Alpha Bank Plc", 123456, "Alpha", "ALPHA"],
    ]
    return workbook(
        "firm.xlsx",
        {
            "Opened": opened,
            "Closed": closed,
            "Percentage upheld": upheld,
            "Consumer Credit": credit,
            "Trading Names": trading,
        },
    )


def test_parses_counts_rates_dates_and_reference(workbook):
    parsed = parse_workbook(_release(workbook), "2026 H1")
    rows = parsed.firm_product.set_index(["firm_name", "product_group"])
    alpha = rows.loc[("Alpha Bank Plc", "Banking and credit cards")]
    assert (alpha.opened, alpha.closed, alpha.pct_upheld) == (900, 800, 0.5)
    beta = rows.loc[("Beta Insurance Ltd", "Insurance & pure protection")]
    assert str(beta.reporting_start) == "2025-10-01"  # the firm's own half-year is kept
    assert beta.opened == 750  # numbers stored as text are read
    assert parsed.firm_reference.frn.tolist() == [123456]
    assert "Grand Total" not in parsed.firm_product.firm_name.values


def test_bad_values_are_set_aside_and_logged(workbook):
    parsed = parse_workbook(_release(workbook), "2026 H1")
    rows = parsed.firm_product.set_index(["firm_name", "product_group"])
    assert rows.loc[("Beta Insurance Ltd", "Insurance & pure protection")].pct_upheld != 1.4
    issues = {issue["issue"] for issue in parsed.issues}
    assert "percentage outside 0 to 100%" in issues
    assert "spreadsheet error value ignored" in issues


def test_rates_are_cleared_when_nothing_was_closed(workbook):
    parsed = parse_workbook(_release(workbook), "2026 H1")
    home = parsed.firm_product.set_index(["firm_name", "product_group"]).loc[
        ("Alpha Bank Plc", "Home finance")
    ]
    assert home.closed == 0 and home.pct_upheld is None or home.pct_upheld != home.pct_upheld


def test_shifted_columns_are_realigned(workbook):
    credit = [
        [
            "",
            "Firm Name",
            "Group",
            "Reporting Frequency",
            "Reporting Period",
            "Complaints Opened",
            "Complaints Closed",
            "Percentage Upheld",
        ],
        [None, "Alpha Bank Plc", "ALPHA", "01 July 2019 to 31 December 2019", "Half Yearly", 7, 8, 0.625],
    ]
    parsed = parse_workbook(_release(workbook, credit_rows=credit), "2019 H2")
    row = parsed.consumer_credit.iloc[0]
    assert (row.received, row.closed, row.pct_upheld, row.frequency) == (7, 8, 0.625, "Half Yearly")
    assert any(issue["issue"] == "columns shifted against header" for issue in parsed.issues)


def test_consumer_credit_upheld_counts_become_shares(workbook):
    credit = [
        [
            "Firm Name",
            "Group",
            "Joint Report",
            "Reporting frequency",
            "Reporting period",
            "Complaints Received",
            "Complaints Closed",
            "Complaints upheld by firm",
        ],
        ["Alpha Bank Plc", "A", "no", "Half Yearly", "2022-01-01 to 2022-06-30", 958, 864, 353],
        ["Beta Insurance Ltd", "B", "no", "Half Yearly", "2022-01-01 to 2022-06-30", 100, 50, 25],
    ]
    parsed = parse_workbook(_release(workbook, credit_rows=credit), "2022 H1")
    credit_rows = parsed.consumer_credit.set_index("firm_name")
    assert credit_rows.upheld_unit.unique().tolist() == ["count"]
    assert credit_rows.loc["Alpha Bank Plc"].pct_upheld == pytest.approx(353 / 864)


def test_consumer_credit_error_cells_are_logged(workbook):
    credit = [
        [
            "Firm Name",
            "Group",
            "Joint Report",
            "Reporting frequency",
            "Reporting period",
            "Complaints Received",
            "Complaints Closed",
            "Complaints upheld by firm",
        ],
        ["Alpha Bank Plc", "A", "no", "Half Yearly", "2019-01-01 to 2019-06-30", 0, 0, "#DIV/0!"],
    ]
    parsed = parse_workbook(_release(workbook, credit_rows=credit), "2019 H1")
    row = parsed.consumer_credit.iloc[0]
    assert row.received == 0 and pd.isna(row.pct_upheld)  # missing, not zero
    logged = [
        i["detail"]
        for i in parsed.issues
        if i["issue"] == "spreadsheet error value ignored" and i["sheet"] == "Consumer Credit"
    ]
    assert logged == ["upheld: #DIV/0!"]
