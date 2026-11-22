"""Load data/processed into SQL Server, create the reporting views and run the data tests.

Connection: Windows authentication to the server in config/settings.toml by
default (localhost\\SQLEXPRESS). Set COMPLAINTS_SQL_SERVER, and for SQL
authentication COMPLAINTS_SQL_USER and COMPLAINTS_SQL_PASSWORD, to override.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .settings import Settings, load_settings

# Load order follows the foreign keys.
TABLES = [
    ("dim_period", "dw"),
    ("dim_product_group", "dw"),
    ("dim_firm", "dw"),
    ("fact_fca_firm_product", "dw"),
    ("fact_fca_consumer_credit", "dw"),
    ("fact_fca_market", "dw"),
    ("fca_market_revisions", "dw"),
    ("firm_map", "dw"),
    ("fact_fos_business_category", "dw"),
    ("fact_fos_business", "dw"),
    ("dq_checks", "dq"),
    ("issues", "dq"),
]
_TARGET = {"dq_checks": "checks", "issues": "issues"}
_PREFERRED_DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server")


def connection_string(settings: Settings, database: str | None = None) -> str:
    import pyodbc

    installed = set(pyodbc.drivers())
    driver = next((d for d in _PREFERRED_DRIVERS if d in installed), None)
    if driver is None:
        raise RuntimeError(f"No SQL Server ODBC driver found. Installed drivers: {sorted(installed)}")
    parts = [
        f"DRIVER={{{driver}}}",
        f"SERVER={settings.sql_server}",
        f"DATABASE={database or settings.sql_database}",
    ]
    if settings.sql_user:
        parts += [f"UID={settings.sql_user}", f"PWD={settings.sql_password or ''}"]
    else:
        parts.append("Trusted_Connection=yes")
    if driver.startswith("ODBC Driver 18"):
        parts.append("TrustServerCertificate=yes")
    return ";".join(parts)


def connect(settings: Settings, database: str | None = None):
    import pyodbc

    return pyodbc.connect(connection_string(settings, database), autocommit=True)


def batches(sql: str) -> list[str]:
    """Split a script on lines that contain only GO."""
    return [
        part.strip()
        for part in re.split(r"^\s*GO\s*$", sql, flags=re.MULTILINE | re.IGNORECASE)
        if part.strip()
    ]


def run_script(cursor, path: Path) -> None:
    for batch in batches(path.read_text(encoding="utf-8")):
        cursor.execute(batch)


def ensure_database(settings: Settings) -> None:
    with connect(settings, "master") as conn:
        name = settings.sql_database.replace("]", "]]")
        conn.cursor().execute(f"IF DB_ID(N'{name}') IS NULL CREATE DATABASE [{name}]")


def _target_columns(cursor, schema: str, table: str) -> list[str]:
    cursor.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
        "AND COLUMNPROPERTY(OBJECT_ID(TABLE_SCHEMA + '.' + TABLE_NAME), COLUMN_NAME, 'IsIdentity') = 0 "
        "ORDER BY ORDINAL_POSITION",
        schema,
        table,
    )
    return [row[0] for row in cursor.fetchall()]


def _values(frame: pd.DataFrame, columns: list[str]) -> list[tuple]:
    frame = frame.rename(columns={"check": "check_name"})
    frame = frame[columns].astype(object).where(frame[columns].notna(), None)
    out = []
    for row in frame.itertuples(index=False):
        values = []
        for value in row:
            if isinstance(value, float) and value.is_integer() and abs(value) < 2**62:
                value = int(value)
            values.append(value)
        out.append(tuple(values))
    return out


def load(settings: Settings | None = None, verbose: bool = True) -> list[tuple[str, int]]:
    """Rebuild the warehouse and return the data-test results as (test, failing rows)."""
    settings = settings or load_settings()
    ensure_database(settings)
    with connect(settings) as conn:
        cursor = conn.cursor()
        cursor.fast_executemany = True
        run_script(cursor, settings.sql / "01_schemas.sql")
        run_script(cursor, settings.sql / "02_tables.sql")
        for name, schema in TABLES:
            table = _TARGET.get(name, name)
            frame = pd.read_csv(settings.processed / f"{name}.csv", keep_default_na=True)
            if "joint_report" in frame:
                frame["joint_report"] = frame["joint_report"].map({True: 1, False: 0, "True": 1, "False": 0})
            columns = _target_columns(cursor, schema, table)
            rows = _values(frame, columns)
            placeholders = ", ".join("?" for _ in columns)
            cursor.executemany(
                f"INSERT INTO {schema}.{table} ({', '.join(columns)}) VALUES ({placeholders})", rows
            )
            if verbose:
                print(f"  loaded {schema}.{table}: {len(rows):,} rows")
        run_script(cursor, settings.sql / "03_views.sql")
        return run_tests(cursor, settings, verbose)


def run_tests(cursor, settings: Settings, verbose: bool = True) -> list[tuple[str, int]]:
    results = []
    for path in sorted((settings.sql / "tests").glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for block in re.split(r"^-- test: ", text, flags=re.MULTILINE)[1:]:
            name, _, query = block.partition("\n")
            cursor.execute(query)
            failing = len(cursor.fetchall())
            results.append((name.strip(), failing))
            if verbose:
                print(
                    f"  {'PASS' if failing == 0 else 'FAIL'}  {name.strip()}"
                    + ("" if failing == 0 else f"  ({failing} rows)")
                )
    return results
