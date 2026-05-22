"""화면 ④ 1차 충격 기업 상세 + shortestPath drill-down."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from nice_poc.api.schemas import FirmDetail, PathResponse

router = APIRouter(prefix="/api", tags=["firms"])


@router.get("/run/{run_id}/firm/{firm_id}", response_model=FirmDetail)
def get_firm(run_id: str, firm_id: str) -> FirmDetail:
    raise HTTPException(status_code=501, detail="data not loaded yet")


@router.get("/run/{run_id}/firm/{firm_id}/path", response_model=PathResponse)
def get_firm_path(
    run_id: str,
    firm_id: str,
    from_: str = Query(..., alias="from", description="출발 기업 ID"),
) -> PathResponse:
    raise HTTPException(status_code=501, detail="data not loaded yet")
