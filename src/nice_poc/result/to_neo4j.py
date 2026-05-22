""":SimulationRun + :IMPACTS 적재. 구현명세서 §5.5, Neo4j 설계서 §4.3.

PoC 1차 규모(≤ 1만 firm) 에서는 UNWIND 한 번이면 충분.
규모 확대 시 ``apoc.periodic.iterate`` 로 전환.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from nice_poc.db import neo4j_session
from nice_poc.result.impact_record import IMPACT_COLUMNS

WRITE_RUN = """
MERGE (run:SimulationRun {run_id: $run_id})
SET run.scenario_id      = $scenario_id,
    run.scenario_group_id = $scenario_group_id,
    run.target_year      = $target_year,
    run.iter             = $iter,
    run.max_iter         = $max_iter,
    run.epsilon          = $epsilon,
    run.spectral_radius_a = $rho_a,
    run.capped_ratio     = $capped_ratio,
    run.status           = 'COMPLETED',
    run.completed_at     = datetime($completed_at)
RETURN run.run_id AS run_id
"""

WRITE_IMPACTS = """
MATCH (run:SimulationRun {run_id: $run_id})
UNWIND $rows AS row
MATCH (f:Firm {firm_id: row.firm_id})
MERGE (run)-[imp:IMPACTS {firm_id: row.firm_id}]->(f)
SET imp.revenue_initial     = row.revenue_initial,
    imp.revenue_propagation = row.revenue_propagation,
    imp.revenue_sum         = row.revenue_sum,
    imp.cost_initial        = row.cost_initial,
    imp.cost_propagation    = row.cost_propagation,
    imp.cost_sum            = row.cost_sum,
    imp.profit_initial      = row.profit_initial,
    imp.profit_propagation  = row.profit_propagation,
    imp.profit_sum          = row.profit_sum,
    imp.impact_score        = row.impact_score,
    imp.capped              = row.capped
"""

LINK_SCENARIO = """
MATCH (s:Scenario {scenario_id: $scenario_id})
MATCH (run:SimulationRun {run_id: $run_id})
MERGE (s)-[:EXECUTED_AS]->(run)
"""


def _rows(
    impact_table: pd.DataFrame,
    *,
    impact_score: pd.Series | None,
    capped_flag: pd.Series | None,
) -> list[dict[str, object]]:
    df = impact_table[list(IMPACT_COLUMNS)].copy()
    df["firm_id"] = df.index.astype(str)
    if impact_score is not None:
        df["impact_score"] = impact_score.reindex(df.index).astype("float64").fillna(0.0)
    else:
        df["impact_score"] = 0.0
    if capped_flag is not None:
        df["capped"] = capped_flag.reindex(df.index).fillna(False).astype(bool)
    else:
        df["capped"] = False
    return df.where(df.notna(), None).to_dict("records")


def write_impacts(
    *,
    run_id: str,
    scenario_id: str | None,
    target_year: int,
    impact_table: pd.DataFrame,
    impact_score: pd.Series | None = None,
    capped_flag: pd.Series | None = None,
    scenario_group_id: str | None = None,
    iter_count: int = 1,
    max_iter: int = 8,
    epsilon: float = 1_000_000.0,
    rho_a: float | None = None,
    capped_ratio: float | None = None,
    database: str | None = None,
) -> dict[str, int | str | None]:
    rows = _rows(impact_table, impact_score=impact_score, capped_flag=capped_flag)
    completed_at = datetime.now(UTC).isoformat()

    with neo4j_session(database=database) as s:
        s.run(
            WRITE_RUN,
            run_id=run_id,
            scenario_id=scenario_id,
            scenario_group_id=scenario_group_id,
            target_year=target_year,
            iter=iter_count,
            max_iter=max_iter,
            epsilon=epsilon,
            rho_a=rho_a,
            capped_ratio=capped_ratio,
            completed_at=completed_at,
        ).consume()

        s.run(WRITE_IMPACTS, run_id=run_id, rows=rows).consume()

        if scenario_id is not None:
            s.run(LINK_SCENARIO, run_id=run_id, scenario_id=scenario_id).consume()

    return {"run_id": run_id, "impacts_written": len(rows), "scenario_id": scenario_id}


__all__ = ["write_impacts"]
