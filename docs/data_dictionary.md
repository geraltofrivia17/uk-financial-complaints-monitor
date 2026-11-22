# Data dictionary

All tables are written to `data/processed/` by `build` and loaded into SQL Server by `load`.

## Dimensions (schema `dw`)

| Table | Key | Columns |
|---|---|---|
| `dim_period` | source, period_label | start_date, end_date, months, min_threshold (Ombudsman publication threshold), note, period_order (year x 10 + half, so one year earlier is order − 10) |
| `dim_product_group` | product_group | sort_order. The FCA's five groups: Banking and credit cards; Decumulation & pensions; Home finance; Insurance & pure protection; Investments |
| `dim_firm` | firm_id | firm_name_latest (latest published name, in a published mixed-case spelling where one exists), firm_group_latest, first_period, last_period, periods_reported, names_used, frn (FCA reference number where published) |

## Facts (schema `dw`)

| Table | Grain | Measures |
|---|---|---|
| `fact_fca_firm_product` | release x firm x firm reporting period x product group | opened, closed, pct_closed_3d, pct_closed_3d_8w, pct_upheld (published); upheld_derived, closed_3d_derived (rate x closed); context_provision (complaints per 1,000 accounts, policies or balances held), context_intermediation (per 1,000 sales); reporting_start, reporting_end, reporting_months; firm_name as published in that release |
| `fact_fca_consumer_credit` | release x firm x reporting period | received, closed, pct_upheld; upheld_unit records whether the release published a share or a count |
| `fact_fca_market` | half-year x product group | opened, closed, closed_within_3d, closed_3d_8w, closed_over_8w, upheld, redress_upheld, redress_not_upheld (£); latest_vintage (release the figure was last published in) |
| `fca_market_revisions` | half-year x product group x measure | first and latest published values and releases, change, change_pct |
| `fact_fos_business_category` | Ombudsman period x business x Ombudsman category | new_cases, pct_upheld; product_group (FCA equivalent, none for funeral plans), firm_id and link_method where linked |
| `fact_fos_business` | Ombudsman period x business | total_new_cases, total_pct_upheld, proactive_settlements (published from 2022) |
| `firm_map` | Ombudsman business name | firm_id, link_method (exact name or reviewed match) |

## Data quality (schema `dq`)

| Table | Content |
|---|---|
| `checks` | check_name, status (pass, warn, fail), actual, expected, detail |
| `issues` | every value or row set aside during parsing, with source, release, sheet, firm and reason |

## Reporting views (schema `rpt`)

| View | Purpose |
|---|---|
| `v_market_half_year` | Market totals with uphold rate, share closed within 3 days and after 8 weeks, redress per upheld complaint |
| `v_coverage` | Firm-level complaints opened as a share of the market total |
| `v_firm_half_year` | Firm rows with the firm's latest name and rank by complaints opened |
| `v_firm_funnel` | Firm uphold rates against the pooled rate, with binomial and adjusted limits and the firm's position |
| `v_fca_vs_ombudsman` | Ombudsman uphold rate next to the firm's own rate for an overlapping FCA period |
| `v_consumer_credit` | Consumer credit complaints by firm |

## Power BI measures

Defined in `powerbi/ComplaintsMonitor.SemanticModel/definition/tables/Measures.tmdl`, in display folders Market, Firms, Funnel, Ombudsman and Data quality. "Selected half-year" measures follow the half-year chosen on the page and default to the latest.
