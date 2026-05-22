-- 아키텍처 설계서 §2.6
-- 시안 ⑥ (산업/본사별 집계) 응답시간 < 200ms 를 위해 사전 집계.

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_impacts_by_sector AS
SELECT
    i.run_id,
    f.sector_code,
    sum(i.revenue_sum) AS revenue_total,
    sum(i.cost_sum)    AS cost_total,
    sum(i.profit_sum)  AS profit_total,
    count(*) FILTER (WHERE i.revenue_sum <> 0) AS firm_count
FROM impacts i
JOIN firms f USING (firm_id)
GROUP BY i.run_id, f.sector_code;

CREATE UNIQUE INDEX IF NOT EXISTS mv_impacts_by_sector_pk
    ON mv_impacts_by_sector (run_id, sector_code);

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_impacts_by_hq AS
SELECT
    i.run_id,
    f.rep_bizno,
    sum(i.revenue_sum) AS revenue_total,
    count(*)           AS firm_count
FROM impacts i
JOIN firms f USING (firm_id)
GROUP BY i.run_id, f.rep_bizno;

CREATE UNIQUE INDEX IF NOT EXISTS mv_impacts_by_hq_pk
    ON mv_impacts_by_hq (run_id, rep_bizno);
