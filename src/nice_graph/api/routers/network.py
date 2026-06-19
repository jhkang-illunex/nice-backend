"""네트워크 분석 라우터 (/api/network/*) — 일반 그래프(네트워크 사이언스) 질의.

흐름: 요청 → ``edge_graph.build_graph()`` (PG SELECT + networkx 조립) →
``analysis/algorithms.py`` 계산 → JSON. 도메인(쇼크) 무관한 **순수 그래프 분석**이다.

엔드포인트 ↔ 알고리즘
  GET /summary                    → algo.summary           그래프 기본 통계
  GET /centrality/degree          → algo.degree_centrality  연결 많은 허브
  GET /centrality/pagerank        → algo.pagerank           흐름 기준 영향력
  GET /centrality/betweenness     → algo.betweenness        병목·중개
  GET /path                       → algo.shortest_path      두 노드 최단경로
  GET /neighbors/{bizno}          → algo.neighbors          N-depth 이웃
  GET /components                 → algo.components          연결 컴포넌트

성능: 호출 1회마다 PG 풀 SELECT + 그래프 재빌드(캐시 없음). 빈번 호출은 캐시 권장
  (별 단계 — Redis 또는 in-process LRU). betweenness 는 O(N·M) 로 가장 무겁다.

⚠️ 데이터 드리프트(중요): 본 라우터가 읽는 ``public.node`` / ``public.edge`` 는 현재
  운영 스키마에서 **0건(빈 테이블)** 이라, 실호출 시 노드/엣지 0 의 빈 결과가 온다.
  실거래 데이터는 ``public.company_edge`` / ``public.company`` 에 있고 그쪽은
  **쇼크 파이프라인(/api/shock/*)** 이 쓴다. 이 드리프트는 담당 범위상 의도적으로
  미수정(상세: ``db/edge_graph.py`` 모듈 docstring). 즉 /api/network/* 는 데이터
  계층이 채워지기 전까지 **구조 검증용 골격** 으로 본다(알고리즘 로직은 정상).
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
    """그래프 빌드 + 에러를 HTTP 상태로 매핑하는 공통 진입 (모든 엔드포인트 공유).

    DB 도달 실패(SQLAlchemyError) → 503, 잘못된 weight 컬럼(ValueError) → 422.
    각 엔드포인트는 보통 ``weight=weight if weighted else None`` 로 호출 — weighted=False
    면 weight 컬럼을 무시(모든 엣지 weight=1.0)하고 구조만 본다.
    """
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
