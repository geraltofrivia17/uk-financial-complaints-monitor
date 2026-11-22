# Changelog

## Unreleased

- Firm names: when a firm's latest name was published in capitals, the most recent mixed-case spelling of the same name is shown instead (for example "National Westminster Bank Plc"). Renames are not undone.
- Consumer credit: spreadsheet error values (`#DIV/0!` in the upheld column, five cells across 2017 H1, 2017 H2 and 2019 H1) are now logged in `issues.csv`; they were already treated as missing.
- Power BI: the funnel scatter uses a logarithmic x-axis and grey for firms within limits; smaller bubbles on the Ombudsman scatter; firm table font reduced so every column fits.
- README: CI badge, tools line, two more Power BI screenshots, clearer counts of name variants and renamed firms.
- CI: the ODBC driver step no longer stops when the runner already has Microsoft's signing key.

## 0.1.0 (22 November 2026)

- FCA firm-level (2016 H2 to 2026 H1), FCA aggregate and Financial Ombudsman data parsed into tidy tables, with data-quality checks and a log of values set aside.
- SQL Server warehouse, reporting views and SQL data tests.
- Analysis: market trends, overdispersion-adjusted funnel plots, firm against Ombudsman outcomes.
- Power BI project (TMDL semantic model).
