from datetime import date

import pytest

from complaints_monitor.names import is_total_row, match_key, name_key
from complaints_monitor.periods import Period, calendar_half, parse_period


@pytest.mark.parametrize(
    "text, start, end",
    [
        ("01 July 2016 to 31 December 2016", date(2016, 7, 1), date(2016, 12, 31)),
        ("2026-01-01 to 2026-06-30", date(2026, 1, 1), date(2026, 6, 30)),
        ("01-01-2020 to 30-06-2020", date(2020, 1, 1), date(2020, 6, 30)),
        ("1 January 18 to 30 June 18", date(2018, 1, 1), date(2018, 6, 30)),
        ("29 August 17 to 28 Feb 18", date(2017, 8, 29), date(2018, 2, 28)),
        ("01-January-2017 to 30-June-2017", date(2017, 1, 1), date(2017, 6, 30)),
    ],
)
def test_every_published_period_layout_parses(text, start, end):
    assert parse_period(text) == Period(start, end)


@pytest.mark.parametrize("value", [None, "", "Half Yearly", "2026 H1", 42, "31 June 2020 to 1 July 2020"])
def test_non_periods_return_none(value):
    assert parse_period(value) is None


def test_labels_are_calendar_halves_only_for_the_fca():
    assert calendar_half("2026 H1") == Period(date(2026, 1, 1), date(2026, 6, 30))
    assert calendar_half("2025 H2").months == 6


def test_overlap_share_is_measured_against_the_other_period():
    ombudsman = Period(date(2025, 7, 1), date(2026, 3, 31))  # the nine-month one-off period
    firm = Period(date(2025, 7, 1), date(2025, 12, 31))
    assert firm.overlap_share(ombudsman) == pytest.approx(184 / 274)


def test_name_keys():
    assert name_key("Cynergy Bank Ltd.") == "CYNERGY BANK LIMITED"
    assert match_key("Cynergy Bank Limited") == match_key("CYNERGY BANK PLC") == "CYNERGY BANK"
    assert match_key("NATIONAL WESTMINSTER BANK PUBLIC LIMITED COMPANY") == match_key(
        "National Westminster Bank Plc"
    )
    assert match_key("U K Insurance Limited") == match_key("UK Insurance Limited")
    assert match_key("Royal & Sun Alliance Insurance plc") == "ROYAL AND SUN ALLIANCE INSURANCE"
    assert name_key("*One Call Insurance Services Ltd") == "ONE CALL INSURANCE SERVICES LIMITED"


@pytest.mark.parametrize(
    "name", ["Grand Total", "TOTALS", "Totals of the above", "(blank)", "Overall total number of complaints"]
)
def test_total_rows_are_recognised(name):
    assert is_total_row(name)
