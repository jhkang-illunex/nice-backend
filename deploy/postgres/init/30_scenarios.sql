-- 아키텍처 설계서 §2.5

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id           VARCHAR(40) PRIMARY KEY,
    scenario_name         TEXT,
    scenario_type         VARCHAR(20),
    scenario_seq          INTEGER,
    version               VARCHAR(10) DEFAULT 'v1',
    scenario_group_id     VARCHAR(40),
    scenario_group_name   TEXT,
    created_at            TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS shocks (
    shock_id                VARCHAR(40) PRIMARY KEY,
    scenario_id             VARCHAR(40) REFERENCES scenarios(scenario_id) ON DELETE CASCADE,
    shock_type              VARCHAR(20),
    target_type             VARCHAR(20),
    target_value            TEXT,
    input_type              VARCHAR(30),
    target_nation           VARCHAR(10)[],

    -- DEMAND
    before_tariff           NUMERIC(6,4),
    after_tariff            NUMERIC(6,4),
    price_value             NUMERIC(8,6),
    pass_through            NUMERIC(4,2) DEFAULT 1.0,
    price_elasticity        NUMERIC(6,2),
    gdp_growth_rate         NUMERIC(6,4),
    income_elasticity       NUMERIC(6,2),
    revenue_value           NUMERIC(8,6),

    -- SUPPLY
    price_m_change_rate     NUMERIC(8,6),
    price_m_elasticity      NUMERIC(6,2),
    import_change           NUMERIC(8,6),
    substitute_elasticity   NUMERIC(4,2) DEFAULT 0.0,
    capacity_value          NUMERIC(8,6),
    cost_value              NUMERIC(8,6),
    price_domestic_value    NUMERIC(8,6),
    profit_value            NUMERIC(8,6),

    duration_month          INTEGER DEFAULT 12
);
