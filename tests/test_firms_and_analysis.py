import numpy as np
import pandas as pd
import pytest

from complaints_monitor import analysis, firms


def test_names_sharing_a_key_in_one_release_stay_apart():
    names = pd.DataFrame(
        {
            "period_label": ["2024 H1", "2024 H1", "2025 H1"],
            "firm_name": ["Ikano Bank", "Ikano Bank AB (publ)", "Cynergy Bank Plc"],
        }
    )
    ids = firms.assign_firm_ids(names).set_index("firm_name").firm_id
    assert ids["Ikano Bank"] != ids["Ikano Bank AB (publ)"]


def test_renamed_firms_are_joined_by_reference_number():
    names = pd.DataFrame(
        {
            "period_label": ["2023 H1", "2025 H1"],
            "firm_name": ["Advanced Payment Solutions Limited", "Zempler Bank Limited"],
        }
    )
    reference = pd.DataFrame(
        {
            "period_label": ["2023 H1", "2025 H1"],
            "frn": [671140, 671140],
            "firm_name": ["Advanced Payment Solutions Limited", "Zempler Bank Limited"],
        }
    )
    ids = firms.assign_firm_ids(names, reference).firm_id.unique()
    assert ids.tolist() == ["ZEMPLER BANK"]


def test_only_exact_names_link_automatically():
    fca = pd.DataFrame(
        {
            "firm_name": ["Sabre Insurance Company Limited", "Monzo Bank Ltd"],
            "firm_id": ["SABRE INSURANCE COMPANY", "MONZO BANK"],
        }
    )
    exact, candidates = firms.candidate_pairs(
        pd.Series(["MONZO BANK LIMITED", "Skyfire Insurance Company Limited"]), fca, threshold=80
    )
    assert exact.firm_id.tolist() == ["MONZO BANK"]
    assert candidates.fos_business_name.tolist() == ["Skyfire Insurance Company Limited"]
    assert firms.firm_map(exact, candidates.assign(decision="reject")).firm_id.tolist() == ["MONZO BANK"]


def _firm_rows(rates, closed=1000):
    return pd.DataFrame(
        {
            "period_label": "2026 H1",
            "firm_id": [f"F{i}" for i in range(len(rates))],
            "product_group": "Home finance",
            "closed": closed,
            "upheld_derived": [round(rate * closed) for rate in rates],
        }
    )


def test_no_extra_width_without_extra_variation():
    rng = np.random.default_rng(0)
    rates = rng.binomial(1000, 0.5, size=40) / 1000
    result = analysis.funnel(_firm_rows(rates), min_closed=100)
    assert result.tau2.iloc[0] == pytest.approx(0, abs=2e-4)


def test_overdispersion_widens_the_limits():
    rates = np.linspace(0.2, 0.8, 30)
    result = analysis.funnel(_firm_rows(rates, closed=100_000), min_closed=100)
    assert result.tau2.iloc[0] > 0
    assert (result.position_binomial != "Within limits").sum() > (
        result.position_adjusted != "Within limits"
    ).sum()
    assert (result.upper_998_adj >= result.upper_998).all()


def test_ombudsman_pairs_need_enough_overlap():
    firm_product = pd.DataFrame(
        {
            "period_label": ["2025 H2", "2025 H1"],
            "firm_id": ["A", "A"],
            "product_group": ["Home finance"] * 2,
            "reporting_start": ["2025-07-01", "2025-01-01"],
            "reporting_end": ["2025-12-31", "2025-06-30"],
            "closed": [500, 500],
            "pct_upheld": [0.4, 0.5],
        }
    )
    fos_category = pd.DataFrame(
        {
            "period_label": ["2025 H2", "2025 H1"],
            "firm_id": ["A", "A"],
            "product_group": ["Home finance"] * 2,
            "category": ["Mortgages & home finance"] * 2,
            "business_name": ["A Ltd"] * 2,
            "start_date": ["2025-07-01", "2025-01-01"],
            "end_date": ["2026-03-31", "2025-06-30"],
            "new_cases": [80, 60],
            "pct_upheld": [0.3, 0.2],
        }
    )
    pairs = analysis.fca_vs_ombudsman(firm_product, fos_category, min_overlap_share=0.75)
    assert pairs.fos_period.tolist() == ["2025 H1"]  # the nine-month period overlaps only 67%
    assert pairs.uphold_gap.iloc[0] == pytest.approx(-0.3)
