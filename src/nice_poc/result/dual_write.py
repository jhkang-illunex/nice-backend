"""Neo4j + PostgreSQL 동시 적재 단일 진입점. 폴리글랏 아키텍처 §5.5.

write_impacts_dual() 가 시뮬레이션 종료 직후 호출되어:
1. PG ``simulation_runs`` UPSERT — FK (impacts.run_id) 보장
2. Neo4j :SimulationRun + :IMPACTS UNWIND (to_neo4j.write_impacts 위임)
3. PG ``impacts`` UPSERT — 9 컬럼 + impact_score + capped
4. PG mv_impacts_by_sector / mv_impacts_by_hq REFRESH (CONCURRENTLY → 실패 시 폴백)

Redis 캐시 warmup (kpi.set_kpi 등) 은 PoC 2차 진입 후 추가한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pandas as pd

from nice_poc.etl.sinks.pg import PgSink
from nice_poc.result import to_neo4j
from nice_poc.result.impact_record import IMPACT_COLUMNS

SIMULATION_RUN_COLUMNS = (
    "run_id",
    "scenario_id",
    "scenario_group_id",
    "target_year",
    "status",
    "iter",
    "max_iter",
    "epsilon",
    "spectral_radius_a",
    "spectral_radius_b",
    "capped_ratio",
    "executed_at",
    "completed_at",
)

IMPACTS_PG_COLUMNS = (
    "run_id",
    "firm_id",
    "scenario_id",
    "scenario_group_id",
    *IMPACT_COLUMNS,
    "impact_score",
    "ui_severity",
    "capped",
)

DEFAULT_MV_NAMES = ("mv_impacts_by_sector", "mv_impacts_by_hq")


@dataclass(frozen=True, slots=True)
class DualWriteReport:
    run_id: str
    pg_runs: int
    neo4j_impacts: int
    pg_impacts: int
    mv_refreshed: tuple[str, ...]


def write_impacts_dual(
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
    rho_b: float | None = None,
    capped_ratio: float | None = None,
    executed_at: datetime | None = None,
    refresh_mv: bool = True,
    mv_names: tuple[str, ...] = DEFAULT_MV_NAMES,
    database: str | None = None,
    pg_sink: PgSink | None = None,
) -> DualWriteReport:
    pg_sink = pg_sink or PgSink()
    completed_at = datetime.now(UTC)
    executed_at = executed_at or completed_at

    runs_df = _runs_row(
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_group_id=scenario_group_id,
        target_year=target_year,
        iter_count=iter_count,
        max_iter=max_iter,
        epsilon=epsilon,
        rho_a=rho_a,
        rho_b=rho_b,
        capped_ratio=capped_ratio,
        executed_at=executed_at,
        completed_at=completed_at,
    )
    pg_runs = pg_sink.upsert(
        "simulation_runs", runs_df, pk=["run_id"], columns=SIMULATION_RUN_COLUMNS
    )

    neo4j_result = to_neo4j.write_impacts(
        run_id=run_id,
        scenario_id=scenario_id,
        target_year=target_year,
        impact_table=impact_table,
        impact_score=impact_score,
        capped_flag=capped_flag,
        scenario_group_id=scenario_group_id,
        iter_count=iter_count,
        max_iter=max_iter,
        epsilon=epsilon,
        rho_a=rho_a,
        capped_ratio=capped_ratio,
        database=database,
    )

    impacts_df = _impacts_rows(
        impact_table,
        run_id=run_id,
        scenario_id=scenario_id,
        scenario_group_id=scenario_group_id,
        impact_score=impact_score,
        capped_flag=capped_flag,
    )
    pg_impacts = pg_sink.upsert(
        "impacts",
        impacts_df,
        pk=["run_id", "firm_id"],
        columns=IMPACTS_PG_COLUMNS,
    )

    refreshed: list[str] = []
    if refresh_mv:
        for mv in mv_names:
            try:
                pg_sink.refresh_mv(mv, concurrently=True)
            except Exception:
                # 첫 refresh 는 CONCURRENTLY 불가. 비-concurrent 로 폴백
                pg_sink.refresh_mv(mv, concurrently=False)
            refreshed.append(mv)

    return DualWriteReport(
        run_id=run_id,
        pg_runs=pg_runs,
        neo4j_impacts=cast(int, neo4j_result["impacts_written"]),
        pg_impacts=pg_impacts,
        mv_refreshed=tuple(refreshed),
    )


def _runs_row(
    *,
    run_id: str,
    scenario_id: str | None,
    scenario_group_id: str | None,
    target_year: int,
    iter_count: int,
    max_iter: int,
    epsilon: float,
    rho_a: float | None,
    rho_b: float | None,
    capped_ratio: float | None,
    executed_at: datetime,
    completed_at: datetime,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "scenario_group_id": scenario_group_id,
                "target_year": target_year,
                "status": "COMPLETED",
                "iter": iter_count,
                "max_iter": max_iter,
                "epsilon": epsilon,
                "spectral_radius_a": rho_a,
                "spectral_radius_b": rho_b,
                "capped_ratio": capped_ratio,
                "executed_at": executed_at,
                "completed_at": completed_at,
            }
        ]
    )


def _impacts_rows(
    impact_table: pd.DataFrame,
    *,
    run_id: str,
    scenario_id: str | None,
    scenario_group_id: str | None,
    impact_score: pd.Series | None,
    capped_flag: pd.Series | None,
) -> pd.DataFrame:
    df = impact_table[list(IMPACT_COLUMNS)].copy()
    df["firm_id"] = df.index.astype(str)
    df["run_id"] = run_id
    df["scenario_id"] = scenario_id
    df["scenario_group_id"] = scenario_group_id

    if impact_score is not None:
        df["impact_score"] = impact_score.reindex(df.index).astype("float64").fillna(0.0)
    else:
        df["impact_score"] = 0.0

    df["ui_severity"] = None  # PoC 1차 미사용 — 분류 룰은 명세서 미정

    if capped_flag is not None:
        df["capped"] = capped_flag.reindex(df.index).fillna(False).astype(bool)
    else:
        df["capped"] = False

    return df.reset_index(drop=True)


__all__ = [
    "DualWriteReport",
    "SIMULATION_RUN_COLUMNS",
    "IMPACTS_PG_COLUMNS",
    "write_impacts_dual",
]
