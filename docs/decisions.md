# Decision log

Each entry records a decision, the alternatives and the evidence. Later entries can overrule earlier ones.

## Scope starts in 2016 H2

**Decision:** use FCA and Ombudsman data from 2016 H2 onwards.
**Why:** the FCA's complaints return changed on 30 June 2016. Earlier firm-level files use different product groups and exclude complaints resolved by close of the third business day, so earlier volumes and rates are not comparable. Files back to 2012 H2 were downloaded and inspected; they are listed in the download log but not used.

## Microsoft stack: SQL Server and Power BI

**Decision:** a SQL Server (T-SQL) warehouse and a Power BI report, with Python for ingestion and checks.
**Why:** SQL Server and Power BI are what most UK banks and insurers use for complaints and conduct reporting, and Power BI is the BI tool most often named in UK analyst adverts. The report reads from SQL views, so the logic is visible and testable in one place.
**Alternatives:** PostgreSQL or DuckDB with Tableau (simpler to run, less typical of the target employers).

## Parse by meaning, not by position

**Decision:** recognise sheets by title, columns by header text, and each row's period by finding the date-range cell.
**Evidence:** across the 20 FCA releases sheets were renamed ("Open", "Opened", "opened"), header rows moved from row 1 to row 4, columns were added (FRN, websites, semester) and removed, and in 2019 H2 the consumer credit sheet's data sit one column left of its header (157 rows). Position-based parsing would have silently read wrong columns.

## Set bad values aside and log them

**Decision:** never coerce a spreadsheet error, text or out-of-range percentage into a number; set it aside and record it in `issues.csv`.
**Why:** a silent zero or a clipped value changes rates without trace. Examples found: a 200% "closed within 3 days", a 120% uphold rate, five rows where the two timeliness shares add up to more than 100%, `#DIV/0!` and `#N/A` cells.

## Consumer credit upheld: share or count

**Decision:** decide per release whether the "upheld" column is a share or a count (most values strictly between 0 and 1 means a share) and convert counts to shares.
**Evidence:** releases 2021 H2 to 2022 H2 and 2023 H2 publish counts; the others publish shares.

## Firm identity across renames

**Decision:** firm key = normalised name without legal suffixes; FCA reference numbers join renamed firms.
**Evidence:** without the FRN step, 22 firms would split into two or more firms across the period (for example Royal & Sun Alliance Insurance and Intact Insurance UK, DAS and ARAG Legal Expenses, Advanced Payment Solutions and Zempler Bank).

## Fuzzy matches are never accepted automatically

**Decision:** link Ombudsman businesses to FCA firms automatically only on equal normalised names; list close names for review.
**Evidence:** of 34 pairs scoring 80 or more on token-sort similarity, 33 were different firms ("Skyfire Insurance Company Limited" and "Sabre Insurance Company Limited", "HFC Bank Limited" and "FCE Bank Plc"). The one accepted pair is a UK branch of the same legal entity.

## Join the two Ombudsman sheets on the normalised name

**Problem found:** loading into SQL Server failed on a duplicate primary key. The Ombudsman's new-cases and resolved-cases sheets sometimes spell the same business in different case ("Bank of Scotland Plc" and "Bank of Scotland plc"); the case-sensitive join in Python produced two half-rows for one business, one with new cases and one with uphold rates (48 category rows across nine periods).
**Decision:** join the sheets on the normalised name and keep the spelling from the new-cases sheet. A pytest on the processed tables now fails on keys that differ only by case.
**Also:** the published-name key columns in SQL Server use a case-sensitive collation (`Latin1_General_100_CS_AS`), because the same business does appear under different spellings in different periods and those rows must stay distinct.

## Periods as dates, and a 75% overlap rule

**Decision:** store every period with real start and end dates; compare an Ombudsman period with a firm's FCA period only when the FCA period covers at least 75% of it.
**Evidence:** the Ombudsman's "H1 2026" is April to September 2026, and its previous period ran nine months (July 2025 to March 2026). Firms' FCA periods follow their own financial years (for example October to March). Matching on labels would compare different months.
**Consequence:** the nine-month Ombudsman period and April to September 2026 are not compared; the latest comparison is 2025 H1.

## Coverage is a range, not an equality

**Decision:** check that firm-level complaints are between 80% and 105% of the market total, not equal to it.
**Why:** the market total covers over 3,000 firms; the firm-level tables cover firms above the publication threshold, on their own reporting dates. A firm whose half-year straddles two calendar halves can push a product group slightly above 100% (2016 H2, home finance).

## Overdispersion-adjusted funnel limits

**Decision:** report funnel positions against limits that include between-firm variance, and show the binomial result alongside.
**Evidence:** in 2026 H1, 243 of 300 firm and product combinations fall outside binomial 99.8% limits; 41 fall outside the adjusted limits. Binomial limits for a firm with 100,000 complaints are about ±0.5 points, so almost any real difference between firms is flagged. The adjustment is the standard approach for comparing institutions (Spiegelhalter, 2005).

## Ombudsman rates weighted by new cases

**Decision:** when combining Ombudsman uphold rates across firms, weight by new cases.
**Why:** the Ombudsman does not publish resolved cases by business. New cases are the closest published measure of volume; this is stated wherever the weighting is used.
