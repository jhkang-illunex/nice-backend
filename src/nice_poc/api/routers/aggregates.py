"""화면 ⑥ 산업/본사 집계 + 화면 ⑦ 시나리오 시계열."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from nice_poc.api.schemas import SectorAggregate, TimeseriesPoint

router = APIRouter(prefix="/api", tags=["aggregates"])


@router.get("/run/{run_id}/by-sector", response_model=list[SectorAggregate])
def by_sector(run_id: str) -> list[SectorAggregate]:
    raise HTTPException(status_code=501, detail="data not loaded yet")


@router.get("/group/{group_id}/timeseries", response_model=list[TimeseriesPoint])
def group_timeseries(group_id: str) -> list[TimeseriesPoint]:
    raise HTTPException(status_code=501, detail="data not loaded yet")
