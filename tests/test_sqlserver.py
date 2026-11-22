"""SQL Server checks. Skipped unless COMPLAINTS_SQL_TESTS=1 (the warehouse must be loaded)."""

import os

import pandas as pd
import pytest

from complaints_monitor import analysis
from complaints_monitor.settings import load_settings

pytestmark = [
    pytest.mark.sqlserver,
    pytest.mark.skipif(os.environ.get("COMPLAINTS_SQL_TESTS") != "1", reason="set COMPLAINTS_SQL_TESTS=1"),
]


@pytest.fixture(scope="module")
def conn():
    from complaints_monitor.warehouse import connect

    with connect(load_settings()) as connection:
        yield connection


def query(conn, sql):
    cursor = conn.cursor()
    cursor.execute(sql)
    columns = [c[0] for c in cursor.description]
    return pd.DataFrame.from_records([tuple(r) for r in cursor.fetchall()], columns=columns)


def test_sql_data_tests_pass(conn):
    from complaints_monitor.warehouse import run_tests

    failures = [name for name, rows in run_tests(conn.cursor(), load_settings(), verbose=False) if rows]
    assert failures == []


def test_sql_funnel_matches_python(conn):
    settings = load_settings()
    python = analysis.funnel(
        pd.read_csv(settings.processed / "fact_fca_firm_product.csv"), settings.min_closed
    )
    sql = query(
        conn,
        "SELECT period_label, firm_id, product_group, tau2, upper_998_adj, position_adjusted "
        "FROM rpt.v_firm_funnel",
    )
    merged = python.merge(sql, on=["period_label", "firm_id", "product_group"], suffixes=("", "_sql"))
    assert len(merged) == len(python) == len(sql)
    assert (merged.tau2 - merged.tau2_sql).abs().max() < 1e-9
    assert (merged.position_adjusted == merged.position_adjusted_sql).all()


def test_sql_ombudsman_pairs_match_python(conn):
    settings = load_settings()
    python = analysis.fca_vs_ombudsman(
        pd.read_csv(settings.processed / "fact_fca_firm_product.csv"),
        pd.read_csv(settings.processed / "fact_fos_business_category.csv"),
        settings.min_overlap_share,
    )
    sql = query(
        conn,
        "SELECT fos_period, firm_id, product_group, fos_business_name AS business_name, fca_period, "
        "uphold_gap FROM rpt.v_fca_vs_ombudsman",
    )
    key = ["fos_period", "firm_id", "product_group", "business_name", "fca_period"]
    assert not python.duplicated(key).any() and not sql.duplicated(key).any()
    merged = python.merge(sql, on=key, suffixes=("", "_sql"))
    assert len(merged) == len(python) == len(sql)
    assert (merged.uphold_gap - merged.uphold_gap_sql.astype(float)).abs().max() < 1e-6
