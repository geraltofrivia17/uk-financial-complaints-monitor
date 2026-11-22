-- Tables are dropped and recreated on every load: the warehouse is rebuilt in
-- full from data/processed, which is itself rebuilt from the published files.

DROP VIEW IF EXISTS rpt.v_fca_vs_ombudsman, rpt.v_firm_funnel, rpt.v_firm_half_year,
    rpt.v_market_half_year, rpt.v_coverage, rpt.v_consumer_credit;
DROP TABLE IF EXISTS dw.fact_fos_business_category, dw.fact_fos_business, dw.fact_fca_firm_product,
    dw.fact_fca_consumer_credit, dw.fact_fca_market, dw.fca_market_revisions, dw.firm_map,
    dw.dim_firm, dw.dim_product_group, dw.dim_period, dq.checks, dq.issues;

CREATE TABLE dw.dim_period (
    source          VARCHAR(10)   NOT NULL,   -- FCA or Ombudsman
    period_label    VARCHAR(7)    NOT NULL,   -- as published, e.g. 2026 H1
    start_date      DATE          NOT NULL,
    end_date        DATE          NOT NULL,
    months          TINYINT       NOT NULL,
    min_threshold   SMALLINT      NULL,       -- Ombudsman publication threshold (cases)
    note            NVARCHAR(200) NULL,
    period_order    INT           NOT NULL,
    CONSTRAINT pk_dim_period PRIMARY KEY (source, period_label),
    CONSTRAINT ck_dim_period_dates CHECK (end_date >= start_date)
);

CREATE TABLE dw.dim_product_group (
    product_group   VARCHAR(40)   NOT NULL CONSTRAINT pk_dim_product_group PRIMARY KEY,
    sort_order      TINYINT       NOT NULL
);

CREATE TABLE dw.dim_firm (
    firm_id             NVARCHAR(200) NOT NULL CONSTRAINT pk_dim_firm PRIMARY KEY,
    firm_name_latest    NVARCHAR(200) NOT NULL,
    firm_group_latest   NVARCHAR(200) NULL,
    first_period        VARCHAR(7)    NOT NULL,
    last_period         VARCHAR(7)    NOT NULL,
    periods_reported    SMALLINT      NOT NULL,
    names_used          SMALLINT      NOT NULL,
    frn                 INT           NULL      -- FCA firm reference number, where published
);

CREATE TABLE dw.fact_fca_firm_product (
    period_label            VARCHAR(7)    NOT NULL,
    firm_id                 NVARCHAR(200) NOT NULL REFERENCES dw.dim_firm (firm_id),
    firm_name               NVARCHAR(200) NOT NULL,   -- name as published in this release
    firm_group              NVARCHAR(200) NULL,
    joint_report            BIT           NULL,
    reporting_start         DATE          NOT NULL,   -- the firm's own reporting half-year
    reporting_end           DATE          NOT NULL,
    reporting_months        TINYINT       NOT NULL,
    product_group           VARCHAR(40)   NOT NULL REFERENCES dw.dim_product_group (product_group),
    opened                  INT           NULL,
    closed                  INT           NULL,
    pct_closed_3d           DECIMAL(9,6)  NULL,
    pct_closed_3d_8w        DECIMAL(9,6)  NULL,
    pct_upheld              DECIMAL(9,6)  NULL,
    upheld_derived          INT           NULL,       -- pct_upheld x closed (the count is not published)
    closed_3d_derived       INT           NULL,
    context_provision       DECIMAL(18,4) NULL,       -- complaints per 1,000 accounts/policies held
    context_intermediation  DECIMAL(18,4) NULL,       -- complaints per 1,000 sales
    source_file             VARCHAR(80)   NOT NULL,
    CONSTRAINT pk_fact_fca_firm_product
        PRIMARY KEY (period_label, firm_id, reporting_start, reporting_end, product_group),
    CONSTRAINT ck_fca_rates CHECK (pct_upheld BETWEEN 0 AND 1 AND pct_closed_3d BETWEEN 0 AND 1
                                   AND pct_closed_3d_8w BETWEEN 0 AND 1)
);

CREATE TABLE dw.fact_fca_consumer_credit (
    period_label        VARCHAR(7)    NOT NULL,
    firm_id             NVARCHAR(200) NOT NULL REFERENCES dw.dim_firm (firm_id),
    firm_name           NVARCHAR(200) NOT NULL,
    firm_group          NVARCHAR(200) NULL,
    frequency           VARCHAR(20)   NULL,
    reporting_start     DATE          NOT NULL,
    reporting_end       DATE          NOT NULL,
    reporting_months    TINYINT       NOT NULL,
    received            INT           NULL,
    closed              INT           NULL,
    upheld_unit         VARCHAR(5)    NOT NULL,   -- how the release published "upheld": share or count
    pct_upheld          DECIMAL(9,6)  NULL,
    upheld_derived      INT           NULL,
    source_file         VARCHAR(80)   NOT NULL,
    CONSTRAINT pk_fact_fca_consumer_credit PRIMARY KEY (period_label, firm_id, reporting_start, reporting_end)
);

CREATE TABLE dw.fact_fca_market (
    period_label        VARCHAR(7)    NOT NULL,
    product_group       VARCHAR(40)   NOT NULL REFERENCES dw.dim_product_group (product_group),
    opened              BIGINT        NULL,
    closed              BIGINT        NULL,
    closed_within_3d    BIGINT        NULL,
    closed_3d_8w        BIGINT        NULL,
    closed_over_8w      BIGINT        NULL,
    upheld              BIGINT        NULL,
    redress_upheld      BIGINT        NULL,   -- pounds
    redress_not_upheld  BIGINT        NULL,   -- pounds
    latest_vintage      VARCHAR(7)    NOT NULL,   -- release the figure was last published in
    CONSTRAINT pk_fact_fca_market PRIMARY KEY (period_label, product_group)
);

CREATE TABLE dw.fca_market_revisions (
    period_label    VARCHAR(7)    NOT NULL,
    product_group   VARCHAR(40)   NOT NULL,
    measure         VARCHAR(30)   NOT NULL,
    first_vintage   VARCHAR(7)    NOT NULL,
    first_value     BIGINT        NOT NULL,
    latest_vintage  VARCHAR(7)    NOT NULL,
    latest_value    BIGINT        NOT NULL,
    vintages        TINYINT       NOT NULL,
    change          BIGINT        NOT NULL,
    change_pct      DECIMAL(12,6) NULL,
    CONSTRAINT pk_fca_market_revisions PRIMARY KEY (period_label, product_group, measure)
);

-- Published names are compared case-sensitively: the Ombudsman spells the same
-- business in different cases in different years ("AXA Insurance UK Plc" and
-- "AXA INSURANCE UK PLC"), and the default case-insensitive collation would
-- treat those as one key.
CREATE TABLE dw.firm_map (
    fos_business_name   NVARCHAR(200) COLLATE Latin1_General_100_CS_AS NOT NULL CONSTRAINT pk_firm_map PRIMARY KEY,
    firm_id             NVARCHAR(200) NOT NULL REFERENCES dw.dim_firm (firm_id),
    link_method         VARCHAR(20)   NOT NULL   -- exact name, or reviewed match
);

CREATE TABLE dw.fact_fos_business_category (
    period_label        VARCHAR(7)    NOT NULL,
    business_name       NVARCHAR(200) COLLATE Latin1_General_100_CS_AS NOT NULL,
    business_group      NVARCHAR(200) NULL,
    category            VARCHAR(40)   NOT NULL,   -- the Ombudsman's product category
    product_group       VARCHAR(40)   NULL REFERENCES dw.dim_product_group (product_group),
    new_cases           INT           NULL,
    pct_upheld          DECIMAL(9,6)  NULL,       -- share of resolved cases upheld for the consumer
    firm_id             NVARCHAR(200) NULL REFERENCES dw.dim_firm (firm_id),
    link_method         VARCHAR(20)   NULL,
    start_date          DATE          NOT NULL,
    end_date            DATE          NOT NULL,
    source_file         VARCHAR(80)   NOT NULL,
    CONSTRAINT pk_fact_fos_business_category PRIMARY KEY (period_label, business_name, category),
    CONSTRAINT ck_fos_rate CHECK (pct_upheld BETWEEN 0 AND 1)
);

CREATE TABLE dw.fact_fos_business (
    period_label            VARCHAR(7)    NOT NULL,
    business_name           NVARCHAR(200) COLLATE Latin1_General_100_CS_AS NOT NULL,
    business_group          NVARCHAR(200) NULL,
    total_new_cases         INT           NULL,
    total_pct_upheld        DECIMAL(9,6)  NULL,
    proactive_settlements   INT           NULL,
    firm_id                 NVARCHAR(200) NULL REFERENCES dw.dim_firm (firm_id),
    link_method             VARCHAR(20)   NULL,
    start_date              DATE          NOT NULL,
    end_date                DATE          NOT NULL,
    source_file             VARCHAR(80)   NOT NULL,
    CONSTRAINT pk_fact_fos_business PRIMARY KEY (period_label, business_name)
);

CREATE TABLE dq.checks (
    check_name  NVARCHAR(200) NOT NULL CONSTRAINT pk_dq_checks PRIMARY KEY,
    status      VARCHAR(4)    NOT NULL,
    actual      NVARCHAR(200) NULL,
    expected    NVARCHAR(200) NULL,
    detail      NVARCHAR(1000) NULL
);

CREATE TABLE dq.issues (
    issue_id     INT IDENTITY(1,1) CONSTRAINT pk_dq_issues PRIMARY KEY,
    source       VARCHAR(20)    NOT NULL,
    period_label VARCHAR(7)     NULL,
    sheet        NVARCHAR(100)  NULL,
    firm_name    NVARCHAR(200)  NULL,
    issue        NVARCHAR(200)  NOT NULL,
    detail       NVARCHAR(400)  NULL
);
