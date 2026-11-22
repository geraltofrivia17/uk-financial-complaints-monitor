-- Reporting views. Rates are always recomputed from counts at the level being
-- reported (never averaged), and every rate has a safe denominator.
-- GO separates the batches (CREATE VIEW must be first in its batch).

CREATE VIEW rpt.v_market_half_year AS
SELECT
    m.period_label,
    p.start_date,
    p.end_date,
    p.period_order,
    m.product_group,
    m.opened,
    m.closed,
    m.upheld,
    m.closed_within_3d,
    m.closed_3d_8w,
    m.closed_over_8w,
    m.redress_upheld,
    m.redress_not_upheld,
    CAST(m.upheld AS FLOAT)            / NULLIF(m.closed, 0) AS uphold_rate,
    CAST(m.closed_within_3d AS FLOAT)  / NULLIF(m.closed, 0) AS share_closed_3d,
    CAST(m.closed_over_8w AS FLOAT)    / NULLIF(m.closed, 0) AS share_closed_over_8w,
    CAST(m.redress_upheld AS FLOAT)    / NULLIF(m.upheld, 0) AS redress_per_upheld,
    m.latest_vintage
FROM dw.fact_fca_market AS m
JOIN dw.dim_period AS p ON p.source = 'FCA' AND p.period_label = m.period_label;
GO

CREATE VIEW rpt.v_coverage AS
-- Firm-level complaints opened as a share of the whole-market total. The two
-- cover different populations (about 290 firms above the publication threshold,
-- against over 3,000 firms), so the share is expected to be below 100%.
SELECT
    m.period_label,
    m.product_group,
    m.opened                                     AS market_opened,
    f.firm_opened,
    f.firms,
    CAST(f.firm_opened AS FLOAT) / NULLIF(m.opened, 0) AS coverage
FROM dw.fact_fca_market AS m
LEFT JOIN (
    SELECT period_label, product_group, SUM(CAST(opened AS BIGINT)) AS firm_opened,
           COUNT(DISTINCT firm_id) AS firms
    FROM dw.fact_fca_firm_product
    GROUP BY period_label, product_group
) AS f ON f.period_label = m.period_label AND f.product_group = m.product_group;
GO

CREATE VIEW rpt.v_firm_half_year AS
SELECT
    f.period_label,
    p.period_order,
    f.firm_id,
    d.firm_name_latest,
    d.firm_group_latest,
    f.product_group,
    f.reporting_start,
    f.reporting_end,
    f.opened,
    f.closed,
    f.pct_upheld,
    f.upheld_derived,
    f.pct_closed_3d,
    f.context_provision,
    RANK() OVER (PARTITION BY f.period_label, f.product_group ORDER BY f.opened DESC) AS rank_by_opened
FROM dw.fact_fca_firm_product AS f
JOIN dw.dim_firm AS d ON d.firm_id = f.firm_id
JOIN dw.dim_period AS p ON p.source = 'FCA' AND p.period_label = f.period_label;
GO

CREATE VIEW rpt.v_firm_funnel AS
-- Funnel plot: each firm's uphold rate against the pooled rate for its product
-- group in the same release. Firms with fewer than 100 closed complaints are
-- left out. Two sets of limits:
--   binomial  chance variation only (z = 1.96 for 95%, 3.09 for 99.8%)
--   adjusted  adds the between-firm variance tau^2 (additive random effects,
--             Spiegelhalter 2005), estimated from z-scores winsorised at the
--             10th and 90th percentiles
-- Must match complaints_monitor.analysis.funnel.
WITH firm AS (
    SELECT period_label, firm_id, product_group,
           SUM(closed) AS closed, SUM(upheld_derived) AS upheld
    FROM dw.fact_fca_firm_product
    WHERE closed >= 100 AND upheld_derived IS NOT NULL
    GROUP BY period_label, firm_id, product_group
), pooled AS (
    SELECT period_label, product_group,
           CAST(SUM(CAST(upheld AS BIGINT)) AS FLOAT) / SUM(CAST(closed AS BIGINT)) AS pooled_rate,
           COUNT(*) AS firms_in_group
    FROM firm
    GROUP BY period_label, product_group
), scored AS (
    SELECT f.*, p.pooled_rate, p.firms_in_group,
           CAST(f.upheld AS FLOAT) / f.closed AS uphold_rate,
           SQRT(p.pooled_rate * (1 - p.pooled_rate) / f.closed) AS se
    FROM firm AS f
    JOIN pooled AS p ON p.period_label = f.period_label AND p.product_group = f.product_group
), z AS (
    SELECT *, (uphold_rate - pooled_rate) / se AS z_score,
           PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY (uphold_rate - pooled_rate) / se)
               OVER (PARTITION BY period_label, product_group) AS z_p10,
           PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY (uphold_rate - pooled_rate) / se)
               OVER (PARTITION BY period_label, product_group) AS z_p90
    FROM scored
), dispersion AS (
    SELECT period_label, product_group,
           COUNT(*) AS k,
           AVG(SQUARE(CASE WHEN z_score < z_p10 THEN z_p10 WHEN z_score > z_p90 THEN z_p90
                           ELSE z_score END)) AS phi,
           SUM(1 / SQUARE(se)) AS sum_w,
           SUM(SQUARE(1 / SQUARE(se))) AS sum_w2
    FROM z
    GROUP BY period_label, product_group
), tau AS (
    SELECT period_label, product_group,
           CASE WHEN k >= 3 AND k * phi > k - 1
                THEN (k * phi - (k - 1)) / (sum_w - sum_w2 / sum_w) ELSE 0 END AS tau2
    FROM dispersion
), limits AS (
    SELECT z.*, t.tau2, SQRT(SQUARE(z.se) + t.tau2) AS se_adjusted
    FROM z
    JOIN tau AS t ON t.period_label = z.period_label AND t.product_group = z.product_group
)
SELECT
    l.period_label,
    per.period_order,
    l.firm_id,
    d.firm_name_latest,
    l.product_group,
    l.closed,
    l.upheld,
    l.uphold_rate,
    l.pooled_rate,
    l.firms_in_group,
    l.tau2,
    l.pooled_rate - 1.96 * l.se AS lower_95,
    l.pooled_rate + 1.96 * l.se AS upper_95,
    l.pooled_rate - 3.09 * l.se AS lower_998,
    l.pooled_rate + 3.09 * l.se AS upper_998,
    l.pooled_rate - 1.96 * l.se_adjusted AS lower_95_adj,
    l.pooled_rate + 1.96 * l.se_adjusted AS upper_95_adj,
    l.pooled_rate - 3.09 * l.se_adjusted AS lower_998_adj,
    l.pooled_rate + 3.09 * l.se_adjusted AS upper_998_adj,
    l.z_score,
    (l.uphold_rate - l.pooled_rate) / l.se_adjusted AS z_adjusted,
    CASE
        WHEN l.uphold_rate > l.pooled_rate + 3.09 * l.se THEN 'Above 99.8% limit'
        WHEN l.uphold_rate < l.pooled_rate - 3.09 * l.se THEN 'Below 99.8% limit'
        WHEN l.uphold_rate > l.pooled_rate + 1.96 * l.se THEN 'Above 95% limit'
        WHEN l.uphold_rate < l.pooled_rate - 1.96 * l.se THEN 'Below 95% limit'
        ELSE 'Within limits'
    END AS position_binomial,
    CASE
        WHEN l.uphold_rate > l.pooled_rate + 3.09 * l.se_adjusted THEN 'Above 99.8% limit'
        WHEN l.uphold_rate < l.pooled_rate - 3.09 * l.se_adjusted THEN 'Below 99.8% limit'
        WHEN l.uphold_rate > l.pooled_rate + 1.96 * l.se_adjusted THEN 'Above 95% limit'
        WHEN l.uphold_rate < l.pooled_rate - 1.96 * l.se_adjusted THEN 'Below 95% limit'
        ELSE 'Within limits'
    END AS position_adjusted
FROM limits AS l
JOIN dw.dim_firm AS d ON d.firm_id = l.firm_id
JOIN dw.dim_period AS per ON per.source = 'FCA' AND per.period_label = l.period_label;
GO

CREATE VIEW rpt.v_fca_vs_ombudsman AS
-- For each firm and product group, the Ombudsman's uphold rate on cases about
-- the firm, next to the firm's own uphold rate in the FCA data for an
-- overlapping reporting period. The Ombudsman sees only complaints that
-- consumers escalated, decided later, so this is a comparison of two stages of
-- the process, not two measurements of the same complaints.
-- A pair is kept only when the firm's FCA period covers at least 75% of the
-- Ombudsman period; where two FCA periods qualify, the larger overlap is used.
-- PPI and funeral plans are excluded: they have no one-to-one FCA product group.
WITH fos AS (
    SELECT b.period_label AS fos_period, b.start_date AS fos_start, b.end_date AS fos_end,
           b.firm_id, b.product_group, b.business_name, b.new_cases, b.pct_upheld AS fos_uphold_rate
    FROM dw.fact_fos_business_category AS b
    WHERE b.firm_id IS NOT NULL AND b.product_group IS NOT NULL AND b.category <> 'PPI'
      AND b.pct_upheld IS NOT NULL
), pairs AS (
    SELECT fos.*, f.period_label AS fca_period, f.reporting_start, f.reporting_end,
           f.closed AS fca_closed, f.pct_upheld AS fca_uphold_rate,
           DATEDIFF(DAY,
               CASE WHEN f.reporting_start > fos.fos_start THEN f.reporting_start ELSE fos.fos_start END,
               CASE WHEN f.reporting_end   < fos.fos_end   THEN f.reporting_end   ELSE fos.fos_end   END) + 1
               AS overlap_days,
           DATEDIFF(DAY, fos.fos_start, fos.fos_end) + 1 AS fos_days
    FROM fos
    JOIN dw.fact_fca_firm_product AS f
      ON f.firm_id = fos.firm_id AND f.product_group = fos.product_group
     AND f.reporting_start <= fos.fos_end AND f.reporting_end >= fos.fos_start
    WHERE f.pct_upheld IS NOT NULL AND f.closed > 0
), best AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY fos_period, firm_id, product_group, business_name
                                 ORDER BY overlap_days DESC, reporting_end DESC) AS pick
    FROM pairs
    WHERE CAST(overlap_days AS FLOAT) / fos_days >= 0.75
)
SELECT
    b.fos_period,
    p.period_order AS fos_period_order,
    b.fos_start,
    b.fos_end,
    b.fca_period,
    b.reporting_start AS fca_start,
    b.reporting_end   AS fca_end,
    b.firm_id,
    d.firm_name_latest,
    b.business_name AS fos_business_name,
    b.product_group,
    b.fca_closed,
    b.fca_uphold_rate,
    b.new_cases       AS fos_new_cases,
    b.fos_uphold_rate,
    b.fos_uphold_rate - b.fca_uphold_rate AS uphold_gap,
    CAST(b.new_cases AS FLOAT) / NULLIF(b.fca_closed, 0) AS fos_cases_per_closed,
    CAST(b.overlap_days AS FLOAT) / b.fos_days AS overlap_share
FROM best AS b
JOIN dw.dim_firm AS d ON d.firm_id = b.firm_id
JOIN dw.dim_period AS p ON p.source = 'Ombudsman' AND p.period_label = b.fos_period
WHERE b.pick = 1;
GO

CREATE VIEW rpt.v_consumer_credit AS
SELECT c.period_label, p.period_order, c.firm_id, d.firm_name_latest, c.frequency,
       c.reporting_start, c.reporting_end, c.received, c.closed, c.pct_upheld, c.upheld_derived
FROM dw.fact_fca_consumer_credit AS c
JOIN dw.dim_firm AS d ON d.firm_id = c.firm_id
JOIN dw.dim_period AS p ON p.source = 'FCA' AND p.period_label = c.period_label;
GO
