"""Command line: python -m complaints_monitor <command>.

download   fetch the published FCA and Ombudsman files (config/sources.csv)
build      parse them into data/processed and run the data-quality checks
load       rebuild the SQL Server warehouse and run the SQL data tests
analyse    write the findings tables and charts to outputs/
report     write README.md from the findings
all        build, load, analyse and report
"""

from __future__ import annotations

import argparse
import sys

from .settings import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m complaints_monitor",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=["download", "build", "load", "analyse", "report", "all"])
    args = parser.parse_args(argv)
    settings = load_settings()
    status = 0
    if args.command == "download":
        from .download import download

        result = download(settings)
        status = int(result.status.str.startswith(("failed", "not a")).any())
    if args.command in ("build", "all"):
        from .build import build

        print("Building processed tables")
        tables = build(settings)
        checks = tables["dq_checks"]
        for row in checks.itertuples():
            print(f"  {row.status.upper():<4}  {row.check}: {row.actual}")
        status = max(status, int((checks.status == "fail").any()))
    if args.command in ("load", "all"):
        from .warehouse import load

        print(f"Loading SQL Server ({settings.sql_server}, database {settings.sql_database})")
        results = load(settings)
        failed = [name for name, rows in results if rows]
        print(f"  {len(results) - len(failed)} of {len(results)} SQL data tests passed")
        status = max(status, int(bool(failed)))
    if args.command in ("analyse", "all"):
        from .analysis import analyse

        print("Writing findings")
        analyse(settings)
    if args.command in ("report", "all"):
        from .report import write_readme

        write_readme(settings)
    return status


if __name__ == "__main__":
    sys.exit(main())
