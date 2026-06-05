"""네트워크 분석 라우터 — public.node / public.edge → networkx → 결과 JSON.

데모용 뼈대. 호출 1회마다 PG SELECT + 그래프 빌드. 빈번한 호출은 캐시 추가
권장 (별 단계 — Redis 또는 in-process LRU).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from nice_graph.analysis import algorithms as algo
from nice_graph.db.edge_graph import _VALID_WEIGHTS, build_graph

router = APIRouter(prefix="/api/network", tags=["network"])
log = logging.getLogger(__name__)


# ─── 응답 모델 (요약) ───────────────────────────────────────────────────────


class SummaryResponse(BaseModel):
    nodes: int = Field(..., description="노드 수", examples=[313])
    edges: int = Field(..., description="엣지 수", examples=[310])
    density: float = Field(..., description="0~1", examples=[0.0031])
    weakly_connected_components: int = Field(..., examples=[1])
    strongly_connected_components: int = Field(..., examples=[308])
    is_dag: bool = Field(..., examples=[True])


class ErrorResponse(BaseModel):
    detail: str


_COMMON_RESPONSES: dict = {
    503: {"model": ErrorResponse, "description": "PostgreSQL 도달 실패."},
    422: {"model": ErrorResponse, "description": "파라미터 검증 실패."},
}


def _load_or_503(trade_year: str | None, weight: str | None = "sly_amt"):
    try:
        return build_graph(trade_year=trade_year, weight=weight)
    except SQLAlchemyError as exc:
        log.exception("db unreachable")
        raise HTTPException(
            status_code=503,
            detail=f"db unreachable: {exc.__class__.__name__}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ─── 엔드포인트 ────────────────────────────────────────────────────────────


@router.get(
    "/summary",
    response_model=SummaryResponse,
    summary="그래프 기본 통계",
    responses=_COMMON_RESPONSES,
)
def summary(
    trade_year: str | None = Query(
        None,
        description="거래연도 필터 (예: '2024'). None 이면 전체.",
        examples=["2024"],
    ),
) -> SummaryResponse:
    g = _load_or_503(trade_year)
    return SummaryResponse(**algo.summary(g))  # type: ignore[arg-type]


@router.get(
    "/centrality/pagerank",
    summary="가중 PageRank 상위 K (Hub 식별)",
    responses=_COMMON_RESPONSES,
)
def centrality_pagerank(
    top_k: int = Query(20, ge=1, le=200),
    alpha: float = Query(0.85, ge=0.1, le=0.99, description="damping factor"),
    trade_year: str | None = Query(None),
    weight: str = Query(
        "sly_amt",
        description=f"가중치 컬럼 — 허용: {', '.join(_VALID_WEIGHTS)}",
    ),
    weighted: bool = Query(True, description="False 면 unweighted PageRank"),
) -> list[dict]:
    g = _load_or_503(trade_year, weight=weight if weighted else None)
    return algo.pagerank(g, top_k=top_k, alpha=alpha, weighted=weighted)


@router.get(
    "/centrality/degree",
    summary="in/out degree centrality 상위 K",
    responses=_COMMON_RESPONSES,
)
def centrality_degree(
    top_k: int = Query(20, ge=1, le=200),
    trade_year: str | None = Query(None),
) -> list[dict]:
    g = _load_or_503(trade_year)
    return algo.degree_centrality(g, top_k=top_k)


@router.get(
    "/centrality/betweenness",
    summary="Betweenness centrality 상위 K",
    responses=_COMMON_RESPONSES,
)
def centrality_betweenness(
    top_k: int = Query(20, ge=1, le=200),
    trade_year: str | None = Query(None),
    weight: str = Query("sly_amt"),
    weighted: bool = Query(True),
    normalized: bool = Query(True),
) -> list[dict]:
    g = _load_or_503(trade_year, weight=weight if weighted else None)
    return algo.betweenness(g, top_k=top_k, weighted=weighted, normalized=normalized)


@router.get(
    "/path",
    summary="두 노드 간 최단 경로 (가중 Dijkstra)",
    responses=_COMMON_RESPONSES,
)
def shortest_path(
    source: str = Query(..., description="시작 bizno"),
    target: str = Query(..., description="도착 bizno"),
    trade_year: str | None = Query(None),
    weight: str = Query("sly_amt"),
    weighted: bool = Query(True),
) -> dict:
    g = _load_or_503(trade_year, weight=weight if weighted else None)
    return algo.shortest_path(g, source=source, target=target, weighted=weighted)


@router.get(
    "/components",
    summary="연결 컴포넌트 통계 (weakly/strongly)",
    responses=_COMMON_RESPONSES,
)
def components(
    top_k: int = Query(5, ge=1, le=50),
    trade_year: str | None = Query(None),
) -> dict:
    g = _load_or_503(trade_year)
    return algo.components(g, top_k=top_k)


@router.get(
    "/neighbors/{bizno}",
    summary="특정 노드의 N-depth BFS 이웃",
    responses=_COMMON_RESPONSES,
)
def neighbors(
    bizno: str = Path(..., description="기업 bizno (사업자번호)"),
    depth: int = Query(1, ge=1, le=4),
    direction: str = Query(
        "both",
        description="'in' | 'out' | 'both' (default).",
    ),
    trade_year: str | None = Query(None),
) -> dict:
    g = _load_or_503(trade_year)
    try:
        return algo.neighbors(g, bizno=bizno, depth=depth, direction=direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
