"""화면 ③ 네트워크 subgraph. Neo4j 그래프 + 레이아웃 좌표 합성 예정."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from nice_poc.api.schemas import NetworkSubgraph

router = APIRouter(prefix="/api", tags=["network"])


@router.get("/run/{run_id}/network", response_model=NetworkSubgraph)
def get_network(
    run_id: str,
    threshold: float = Query(0.0, ge=0.0, description="edge weight 컷오프"),
    top: int = Query(200, ge=1, le=5000, description="노드 상한"),
    year: int | None = Query(None, ge=1900, le=2100),
) -> NetworkSubgraph:
    raise HTTPException(status_code=501, detail="data not loaded yet")
