"""화면 ① KPI 6 카드. 데이터 적재 후 PG impacts 집계로 구현 예정."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from nice_poc.api.schemas import KpiCard

router = APIRouter(prefix="/api", tags=["kpi"])


@router.get("/run/{run_id}/kpi", response_model=KpiCard)
def get_kpi(run_id: str) -> KpiCard:
    raise HTTPException(status_code=501, detail="data not loaded yet")
