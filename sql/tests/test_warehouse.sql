-- Data tests. Each test is a query named by a comment line "-- test: <name>"
-- and passes when it returns no rows. sql/run_tests is run by
-- `python -m complaints_monitor load` after the views are created.

-- test: market totals reconcile closed to its three timeliness bands
SELECT period_label, product_group FROM dw.fact_fca_market
WHERE closed <> closed_within_3d + closed_3d_8w + closed_over_8w;

-- test: every FCA half-year from 2016 H2 to 2026 H1 has market totals for all five product groups
SELECT p.period_label FROM dw.dim_period AS p
LEFT JOIN dw.fact_fca_market AS m ON m.period_label = p.period_label
WHERE p.source = 'FCA'
GROUP BY p.period_label HAVING COUNT(m.product_group) <> 5;

-- test: firm-level complaints opened are between 80% and 105% of the market total in every half-year
SELECT period_label FROM rpt.v_coverage
GROUP BY period_label
HAVING CAST(SUM(firm_opened) AS FLOAT) / SUM(market_opened) NOT BETWEEN 0.80 AND 1.05;

-- test: upheld counts derived from firm rates never exceed closed
SELECT period_label, firm_id, product_group FROM dw.fact_fca_firm_product WHERE upheld_derived > closed;

-- test: firm reporting periods end inside or next to their release half-year
SELECT f.period_label, f.firm_id FROM dw.fact_fca_firm_product AS f
JOIN dw.dim_period AS p ON p.source = 'FCA' AND p.period_label = f.period_label
WHERE f.reporting_end < DATEADD(MONTH, -12, p.start_date) OR f.reporting_end > DATEADD(MONTH, 1, p.end_date);

-- test: every Ombudsman period has a real start and end date and no two periods overlap
SELECT a.period_label FROM dw.dim_period AS a
JOIN dw.dim_period AS b ON b.source = a.source AND b.period_label <> a.period_label
 AND a.start_date <= b.end_date AND a.end_date >= b.start_date
WHERE a.source = 'Ombudsman';

-- test: funnel limits contain the pooled rate, and adjusted limits are at least as wide as binomial ones
SELECT period_label, firm_id, product_group FROM rpt.v_firm_funnel
WHERE NOT (lower_998 <= lower_95 AND lower_95 <= pooled_rate AND pooled_rate <= upper_95 AND upper_95 <= upper_998)
   OR NOT (lower_998_adj <= lower_998 + 1e-12 AND upper_998_adj >= upper_998 - 1e-12);

-- test: FCA and Ombudsman pairs overlap by at least 75% of the Ombudsman period
SELECT fos_period, firm_id FROM rpt.v_fca_vs_ombudsman WHERE overlap_share < 0.75;

-- test: one FCA period is chosen per firm, product group and Ombudsman period
SELECT fos_period, firm_id, product_group, fos_business_name FROM rpt.v_fca_vs_ombudsman
GROUP BY fos_period, firm_id, product_group, fos_business_name HAVING COUNT(*) > 1;

-- test: no data-quality check failed during the build
SELECT check_name FROM dq.checks WHERE status = 'fail';
