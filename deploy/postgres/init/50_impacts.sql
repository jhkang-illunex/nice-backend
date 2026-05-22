-- 아키텍처 설계서 §2.3 / §2.3.1
-- impacts 는 firms · simulation_runs · scenarios 모두에 의존하므로 마지막에 적재.

CREATE TABLE IF NOT EXISTS impacts (
    run_id                 VARCHAR(40) NOT NULL,
    firm_id                VARCHAR(6)  NOT NULL,
    scenario_id            VARCHAR(40),
    scenario_group_id      VARCHAR(40),

    -- v1.1 보강: initial / propagation / sum 9개
    revenue_initial        NUMERIC(18,2),
    revenue_propagation    NUMERIC(18,2),
    revenue_sum            NUMERIC(18,2),
    cost_initial           NUMERIC(18,2),
    cost_propagation       NUMERIC(18,2),
    cost_sum               NUMERIC(18,2),
    profit_initial         NUMERIC(18,2),
    profit_propagation     NUMERIC(18,2),
    profit_sum             NUMERIC(18,2),

    impact_score           NUMERIC(10,4),
    ui_severity            VARCHAR(10),
    capped                 BOOLEAN DEFAULT FALSE,
    created_at             TIMESTAMP DEFAULT now(),

    PRIMARY KEY (run_id, firm_id),
    FOREIGN KEY (firm_id) REFERENCES firms(firm_id),
    FOREIGN KEY (run_id)  REFERENCES simulation_runs(run_id) ON DELETE CASCADE
);

-- §2.3.1 정렬·집계 인덱스
CREATE INDEX IF NOT EXISTS impacts_run_revenue_idx
    ON impacts (run_id, abs(revenue_sum) DESC);

CREATE INDEX IF NOT EXISTS impacts_group_idx
    ON impacts (scenario_group_id);
