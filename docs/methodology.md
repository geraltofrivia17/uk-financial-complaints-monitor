# Methodology

## Sources

| Source | Releases used | Grain | Notes |
|---|---|---|---|
| FCA firm-level complaints data | 2016 H2 to 2026 H1 (20) | Firm x product group x firm reporting period | Firms above the publication threshold (500 complaints in a half-year or 1,000 in a year). Opened and closed counts; closed within 3 days, 3 days to 8 weeks and upheld as percentages; complaints per 1,000 accounts or sales. Consumer credit in a separate table. |
| FCA aggregate complaints data | 2022 H1 to 2026 H1 (9 releases, each covering about ten half-years) | Product group x half-year | All firms that reported complaints (over 3,000). Opened, closed by timeliness band, upheld, redress. |
| Financial Ombudsman half-yearly business data | 2016 H2 to 2026 H1 (20) | Business x Ombudsman product category x period | Businesses with at least 30 new and 30 resolved cases (45 in the nine-month period to March 2026). New cases and the share of resolved cases upheld. |

## Pipeline

1. **Download** (`download`): the files listed in `config/sources.csv`, with a browser-like user agent because both sites refuse some automated clients.
2. **Parse** (`build`): each workbook is read sheet by sheet. Sheets are recognised from their titles and columns from their header text, never from position, because both have moved between releases. Each row's reporting period is found by locating the cell that holds a date range: in one release a sheet's data columns are shifted one place against its header, and the period cell is used to realign the row.
3. **Clean**: blank cells, spreadsheet error values (`#DIV/0!`, `#N/A`) and text are set aside rather than coerced. Percentages outside 0 to 100% are set aside. Rows where both timeliness shares add up to more than 100% have both set aside. Every one of these is written to `data/processed/issues.csv` with the release, sheet, firm and value.
4. **Identify firms**: see below.
5. **Check**: the data-quality checks in `dq_checks.csv`. A failing check stops the build.
6. **Load** (`load`): the processed tables into SQL Server, then the reporting views, then the SQL data tests.
7. **Analyse and report** (`analyse`, `report`).

## Firm identity

- A firm's key is its name upper-cased, with punctuation removed, "LTD" spelt out, "Public Limited Company" shortened to "PLC" and legal suffixes (Limited, PLC, SA, SE...) removed.
- If two different names share a key in the same release, both keep their full names as keys, so two genuinely different firms are never merged (for example "Ikano Bank" and "Ikano Bank AB (publ)" in the same release).
- From 2023 H1 the FCA's trading-names sheet gives each firm's reference number (FRN). Where one FRN appears under two names, the firm was renamed; all its rows take the key of the most recent name. This joins, for example, Advanced Payment Solutions and Zempler Bank.

## Linking FCA firms to Ombudsman businesses

The two publishers share no identifier. An Ombudsman business is linked to an FCA firm when their keys are equal under any name the FCA firm has used. For the rest, the closest FCA name is found with a token-sort similarity score (rapidfuzz). Pairs scoring 80 or more are written to `data/reference/firm_map_review.csv`, and only pairs marked `accept` are used. Almost all close pairs were different firms with generic names in common, which is why fuzzy scores are never accepted automatically.

## Periods

- FCA releases are labelled by calendar half-year, but each firm reports on its own financial half-year, so every firm row keeps its own start and end dates.
- The Ombudsman published calendar half-years until a nine-month period from July 2025 to March 2026, and then moved to April to September (its "H1 2026") and October to March. Its periods are stored with their real dates in `config/fos_periods.csv`.
- A firm's FCA figures are compared with an Ombudsman period only if the firm's reporting period covers at least 75% of the Ombudsman period. Where two FCA periods qualify, the larger overlap is used. The nine-month period cannot reach 75% with a six-month FCA period and is not compared. Nor is April to September 2026 yet: the FCA data runs to June 2026.

## Measures

- **Uphold rate**: upheld / closed, recomputed from counts at whatever level is shown, never averaged across firms.
- **Firm upheld counts** are not published; they are derived as the published rate x closed. As a check, the derived counts summed over firms are compared with the FCA's market totals (`dq_checks.csv`).
- **Ombudsman uphold rates** are published without the number of resolved cases behind them. Where they are combined across firms they are weighted by new cases, an approximation stated wherever it is used.

## Funnel plots

For each release and product group, firms with at least 100 closed complaints are compared with the pooled uphold rate *p0* (total upheld / total closed).

- Binomial limits: *p0 ± z·s*, with *s = sqrt(p0(1 − p0) / n)*, *z* = 1.96 (95%) and 3.09 (99.8%).
- These assume every firm has the same underlying rate. With tens of thousands of complaints per firm the limits become very narrow and most large firms fall outside them (243 of 300 in 2026 H1). The real differences between firms' products and customers are overdispersion, not signal.
- Adjusted limits use an additive random-effects model (Spiegelhalter, 2005, *Funnel plots for comparing institutional performance*, Statistics in Medicine 24). The z-scores are winsorised at the 10th and 90th percentiles, the dispersion factor *φ* is their mean square, and the between-firm variance is *τ² = (kφ − (k − 1)) / (Σw − Σw²/Σw)* with *w = 1/s²* (zero when *kφ ≤ k − 1*). Limits become *p0 ± z·sqrt(s² + τ²)*.
- The same calculation is implemented in Python (`analysis.funnel`) and T-SQL (`rpt.v_firm_funnel`); `tests/test_sqlserver.py` checks they agree.

## Revisions

Each aggregate release restates about ten earlier half-years. All releases are read; the latest published value of each figure is used, and every figure whose value changed between releases is kept in `fca_market_revisions.csv`.
