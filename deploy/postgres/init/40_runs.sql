-- 아키텍처 설계서 §2.4

CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id               VARCHAR(40) PRIMARY KEY,
    scenario_id          VARCHAR(40) REFERENCES scenarios(scenario_id),
    scenario_group_id    VARCHAR(40),
    target_year          INTEGER,
    status               VARCHAR(20),
    iter                 INTEGER,
    max_iter             INTEGER DEFAULT 8,
    epsilon              NUMERIC(18,2) DEFAULT 1000000,
    spectral_radius_a    NUMERIC(8,6),
    spectral_radius_b    NUMERIC(8,6),
    capped_ratio         NUMERIC(5,4),
    executed_at          TIMESTAMP DEFAULT now(),
    completed_at         TIMESTAMP
);
