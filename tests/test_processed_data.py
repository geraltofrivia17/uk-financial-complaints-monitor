"""Checks on the committed processed tables (data/processed)."""

import pandas as pd
import pytest

from conftest import ROOT

PROCESSED = ROOT / "data" / "processed"
pytestmark = pytest.mark.skipif(not (PROCESSED / "dq_checks.csv").exists(), reason="run the build first")


def read(name):
    return pd.read_csv(PROCESSED / f"{name}.csv")


def test_no_quality_check_failed():
    checks = read("dq_checks")
    assert not (checks.status == "fail").any(), checks[checks.status == "fail"].to_string()


def test_keys_are_unique():
    assert (
        not read("fact_fca_firm_product")
        .duplicated(["period_label", "firm_id", "reporting_start", "reporting_end", "product_group"])
        .any()
    )
    assert not read("fact_fca_market").duplicated(["period_label", "product_group"]).any()
    assert (
        not read("fact_fos_business_category").duplicated(["period_label", "business_name", "category"]).any()
    )
    assert read("dim_firm").firm_id.is_unique


def test_every_fact_firm_is_in_the_firm_dimension():
    firm_ids = set(read("dim_firm").firm_id)
    assert set(read("fact_fca_firm_product").firm_id) <= firm_ids
    assert set(read("fact_fos_business_category").firm_id.dropna()) <= firm_ids


def test_ombudsman_periods_never_overlap():
    periods = read("dim_period")
    fos = periods[periods.source == "Ombudsman"].sort_values("start_date")
    assert (
        pd.to_datetime(fos.start_date).iloc[1:].values > pd.to_datetime(fos.end_date).iloc[:-1].values
    ).all()


def test_no_keys_differ_only_by_case():
    """SQL Server compares text case-insensitively by default; keys must not collide there."""
    for name, keys in [
        ("fact_fos_business_category", ["period_label", "business_name", "category"]),
        ("fact_fos_business", ["period_label", "business_name"]),
        ("dim_firm", ["firm_id"]),
    ]:
        lowered = read(name)[keys].astype(str).apply(lambda column: column.str.lower())
        assert not lowered.duplicated().any(), name
