-- UK Financial Complaints Monitor: schemas.
--   dw   conformed dimensions and fact tables, loaded from data/processed
--   rpt  reporting views read by Power BI
--   dq   data-quality results and the log of values set aside
IF SCHEMA_ID('dw') IS NULL EXEC('CREATE SCHEMA dw');
IF SCHEMA_ID('rpt') IS NULL EXEC('CREATE SCHEMA rpt');
IF SCHEMA_ID('dq') IS NULL EXEC('CREATE SCHEMA dq');
