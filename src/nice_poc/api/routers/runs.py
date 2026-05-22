"""화면 ② 시뮬 실행 + 화면 ⑤ 영향 기업 리스트."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from nice_poc.api.schemas import FirmImpact, Paginated, RunCreate, RunOut

router = APIRouter(prefix="/api", tags=["runs"])


@router.post("/run", response_model=RunOut, status_code=202)
def create_run(payload: RunCreate) -> RunOut:
    raise HTTPException(status_code=501, detail="data not loaded yet")


@router.get("/run/{run_id}/firms", response_model=Paginated[FirmImpact])
def list_firms(
    run_id: str,
    sort: str = Query("revenue", description="정렬 키: revenue|cost|profit|severity"),
    page: int = Query(1, ge=1),
    size: int = Query(100, ge=1, le=1000),
) -> Paginated[FirmImpact]:
    raise HTTPException(status_code=501, detail="data not loaded yet")
