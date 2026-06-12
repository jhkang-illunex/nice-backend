"""쇼크 파급 라우터 — fetch_subgraph + propagate(mock) + extract_first_target.

POST /api/shock/fetch_subgraph         HS → 시드 → N차 확장 → nodes/edges
POST /api/shock/propagate              [MOCK] init 충격 그대로 반환
POST /api/shock/extract_first_target   LLM 분류 → HIGH+MEDIUM bizno 만
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from nice_graph.shock import (
    extract_first_target as _extract_first_target,
)
from nice_graph.shock import (
    fetch_subgraph as _fetch_subgraph,
)
from nice_graph.shock import (
    propagate_shock as _propagate_shock,
)

router = APIRouter(prefix="/api/shock", tags=["shock"])
log = logging.getLogger(__name__)


# ─── 스키마 — fetch_subgraph ──────────────────────────────────────────────


class FetchSubgraphRequest(BaseModel):
    hscode: str = Field(
        ...,
        min_length=4,
        max_length=10,
        description="HS 6 또는 10자리 digit string. 10자리면 앞 6자리 사용.",
        examples=["3801300000"],
    )
    n_of_child: int = Field(
        3, ge=1, le=6, description="N차 확장 깊이.", examples=[3]
    )
    mode: Literal["BFS", "DFS"] = Field(
        "BFS",
        description="child 확장 방식. 결과 set 동일, 알고리즘만 다름.",
    )


class NodeOut(BaseModel):
    bizno: str = Field(..., examples=["1018116406"])
    upchecd: str | None = Field(None, examples=["380130"])


class EdgeOut(BaseModel):
    from_bizno: str = Field(..., examples=["1018116406"])
    to_bizno: str = Field(..., examples=["1130452404"])
    years_rate: dict[str, float] = Field(
        default_factory=dict,
        description="source 의 연도별 outgoing 중 비중 (연도별 Σ=1).",
        examples=[{"2024": 0.6, "2025": 0.4}],
    )
    all_rate: float = Field(
        ...,
        description="source 의 outgoing 행 정규화 (source 당 Σ=1).",
        examples=[0.12],
    )


class FetchSubgraphResponse(BaseModel):
    nodes: list[NodeOut]
    edges: list[EdgeOut]


# ─── 스키마 — propagate ───────────────────────────────────────────────────


class EdgePropagateRequest(BaseModel):
    from_bizno: str
    to_bizno: str
    rate: float = Field(..., ge=0.0, description="propagation weight.")


class PropagateRequest(BaseModel):
    edges: list[EdgePropagateRequest]
    init_sub_graph: dict[str, float] = Field(
        ...,
        description="{bizno: initial_shock}.",
        examples=[{"1018116406": 1000000.0}],
    )


class ShockRowOut(BaseModel):
    bizno: str
    shock: float


class PropagateResponse(BaseModel):
    shock_list: list[ShockRowOut]
    total_shock: float
    iterations: int = Field(
        ..., description="실제 진행한 round 수 (epsilon 컷오프 또는 max_iter 도달)."
    )
    converged: bool = Field(
        ...,
        description="True = epsilon 으로 자연 종료. False = max_iter 도달 (ρ(R) ≥ 1 의심).",
    )


# ─── 스키마 — extract_first_target ────────────────────────────────────────


class ExtractFirstTargetRequest(BaseModel):
    node_list: list[str] = Field(
        ...,
        description="bizno 문자열 리스트.",
        examples=[["1018116406", "1130452404"]],
    )
    hscode: str = Field(
        ...,
        min_length=4,
        max_length=10,
        description=(
            "충격 원인 HS6/HS10. LLM 이 *이 HS 의 외생 충격이 각 기업에 얼마나 "
            "영향을 줄지* 를 판단할 때 비교 기준으로 사용. 6/10 자리 모두 허용."
        ),
        examples=["3801300000"],
    )
    trade_year: str | None = Field(
        None,
        description=(
            "ra603 메타 (충격 HS 의 산업분류 비중) 조회 연도. None 이면 메타 skip — "
            "system prompt 토큰 절감, 시나리오 컨텍스트는 약화."
        ),
        examples=["2024"],
    )


class ExtractFirstTargetResponse(BaseModel):
    node_list: list[str] = Field(
        ..., description="LLM 이 HIGH+MEDIUM 으로 분류한 bizno."
    )


# ─── 엔드포인트 ───────────────────────────────────────────────────────────


_COMMON_RESPONSES: dict = {
    503: {"description": "PostgreSQL 또는 LLM 백엔드 도달 실패."},
}


@router.post(
    "/fetch_subgraph",
    response_model=FetchSubgraphResponse,
    summary="HS → 시드 → N차 확장 그래프 조회",
    responses=_COMMON_RESPONSES,
)
def fetch_subgraph(req: FetchSubgraphRequest) -> FetchSubgraphResponse:
    try:
        sg = _fetch_subgraph(
            req.hscode, n_of_child=req.n_of_child, mode=req.mode
        )
    except SQLAlchemyError as exc:
        log.exception("fetch_subgraph db error")
        raise HTTPException(
            status_code=503,
            detail=f"db unreachable: {exc.__class__.__name__}",
        ) from exc
    return FetchSubgraphResponse(
        nodes=[NodeOut(**n) for n in sg.nodes],
        edges=[EdgeOut(**e) for e in sg.edges],
    )


@router.post(
    "/propagate",
    response_model=PropagateResponse,
    summary="쇼크 전파 — round-by-round 거듭제곱급수 합 (Σ_k R^k @ init)",
)
def propagate(req: PropagateRequest) -> PropagateResponse:
    result = _propagate_shock(
        edges=[e.model_dump() for e in req.edges],  # type: ignore[arg-type]
        init_sub_graph=req.init_sub_graph,
    )
    return PropagateResponse(
        shock_list=[ShockRowOut(**r) for r in result.shock_list],
        total_shock=result.total_shock,
        iterations=result.iterations,
        converged=result.converged,
    )


@router.post(
    "/extract_first_target",
    response_model=ExtractFirstTargetResponse,
    summary="LLM 분류 → 1차 충격 대상 bizno 만 반환",
    responses=_COMMON_RESPONSES,
)
def extract_first_target(
    req: ExtractFirstTargetRequest,
) -> ExtractFirstTargetResponse:
    try:
        primary = _extract_first_target(
            req.node_list,
            hscode=req.hscode,
            trade_year=req.trade_year,
        )
    except SQLAlchemyError as exc:
        log.exception("extract_first_target db error")
        raise HTTPException(
            status_code=503,
            detail=f"db unreachable: {exc.__class__.__name__}",
        ) from exc
    return ExtractFirstTargetResponse(node_list=primary)
