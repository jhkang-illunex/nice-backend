"""쇼크 파급 라우터 — 단계별 엔드포인트 (운영자가 따로 호출·검수, chain 미제공).

POST /api/shock/select_primary         [2단계] HS → ra603 거래구성 → 1차 기업 (bizno,upchecd,score)
POST /api/shock/assemble               [3단계] 시드 → 3depth 확장 → 전파입력 (복합키 edges/init/nodes)
POST /api/shock/propagate              [4단계] 거듭제곱급수 전파 (Σ_k R^k @ init)
POST /api/shock/fetch_subgraph         (조회용) HS → 시드 → N차 확장 → nodes/edges (years_rate 포함)
POST /api/shock/extract_first_target   (대안) LLM 분류 → HIGH+MEDIUM bizno 만
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from nice_graph.shock import (
    RandomOverrideSpec as _RandomOverrideSpec,
)
from nice_graph.shock import (
    assemble_propagation_input as _assemble,
)
from nice_graph.shock import (
    extract_first_target as _extract_first_target,
)
from nice_graph.shock import (
    fetch_subgraph as _fetch_subgraph,
)
from nice_graph.shock import (
    propagate_shock as _propagate_shock,
)
from nice_graph.shock import (
    run_scenario as _run_scenario,
)
from nice_graph.shock import (
    select_primary_firms as _select_primary_firms,
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


# ─── 스키마 — select_primary (2단계) ──────────────────────────────────────


class SelectPrimaryRequest(BaseModel):
    hscode: str = Field(
        ..., min_length=4, max_length=10,
        description="충격 HS. 4/6/10 자리 digit string (구분자 허용).",
        examples=["8481"],
    )
    year: str | None = Field(None, description="bse_yr 필터. None=전체 연도.", examples=["2024"])
    exim: str | None = Field(None, description="tseximdivcd 필터('0'/'3'). None=전체 방향.")
    top_k: int | None = Field(None, ge=1, description="상위 K 개만. None=전체.", examples=[10])
    min_ratio: float = Field(0.0, ge=0.0, le=100.0, description="exposure_ratio(%) 하한.")
    ratio_weight: float = Field(0.5, ge=0.0, description="점수 가중치 — 거래비율.")
    tier_weight: float = Field(0.5, ge=0.0, description="점수 가중치 — 금액규모.")


class FirmOut(BaseModel):
    upchecd: str
    bizno: str | None = Field(None, description="company 매핑 (1:1). None 이면 시드 불가.")
    korentrnm: str | None = None
    exposure_ratio: float = Field(..., description="avg(거래구성 비율 %), 0~100.")
    amount_tier: int = Field(..., description="max(금액구간 코드), 0~7.")
    score: float = Field(..., description="ratio_weight·(ratio/100)+tier_weight·(tier/7).")
    n_cells: int = Field(..., description="집계된 (연도,방향) 셀 수.")


class SelectPrimaryResponse(BaseModel):
    hscode: str
    hs_digits: int
    year: str | None
    exim: str | None
    firms: list[FirmOut] = Field(..., description="score 내림차순.")


# ─── 스키마 — assemble (3단계) ────────────────────────────────────────────


class SeedIn(BaseModel):
    bizno: str = Field(..., examples=["5948801875"])
    upchecd: str | None = Field(None, examples=["184084"])
    shock: float = Field(1.0, description="이 시드의 초기 충격 (select_primary 의 score 등).")


class AssembleRequest(BaseModel):
    seeds: list[SeedIn] = Field(..., description="1차 기업 (bizno,upchecd,shock).")
    depth: int = Field(3, ge=1, le=6, description="확장 깊이.")
    trade_year: str | None = Field(None, description="거래연도 필터. None=전 연도.")
    within_subgraph: bool = Field(
        True, description="rate 분모: 서브그래프 내(True,Σ_out=1) / 전체 outgoing(False, leakage)."
    )
    damping: float = Field(
        0.85, gt=0.0, le=1.0,
        description="홉당 감쇠율 α. rate=α·(amt/denom) → ρ≤α<1 수렴 보장.",
    )


class AssembledEdgeOut(BaseModel):
    from_bizno: str = Field(..., description="복합키 node_id 'bizno|upchecd'.")
    to_bizno: str = Field(..., description="복합키 node_id 'bizno|upchecd'.")
    rate: float


class AssembledNodeOut(BaseModel):
    node_id: str
    bizno: str
    upchecd: str | None
    korentrnm: str | None
    is_seed: bool
    seed_shock: float


class AssembleResponse(BaseModel):
    edges: list[AssembledEdgeOut] = Field(..., description="propagate 의 edges 로 그대로 전달.")
    init_sub_graph: dict[str, float] = Field(..., description="{node_id: shock} — propagate 의 init.")
    nodes: list[AssembledNodeOut] = Field(..., description="node_id ↔ (bizno,upchecd,기업명) 매핑.")
    depth: int
    rate_kind: str
    within_subgraph: bool
    damping: float
    warnings: list[str]


# ─── 스키마 — scenario (관세 충격 / 거래 변화) ────────────────────────────


class EdgeOverrideIn(BaseModel):
    from_bizno: str = Field(..., description="저장방향 셀러 bizno.", examples=["1018116406"])
    to_bizno: str = Field(..., description="저장방향 바이어 bizno.", examples=["1130452404"])
    factor: float = Field(
        ..., ge=0.0, le=3.0,
        description=(
            "이 (셀러→바이어) 거래 비중에 곱할 인자 g = 1+증감율. "
            "0.8=20%감소 · 1.0=무변화 · 1.1=10%증가. g<1 감소(수렴) / "
            "g>1 증가(Σ_out>1 시 수렴 보장 깨짐, 경고)."
        ),
        examples=[0.8, 1.1],
    )


class RandomOverrideIn(BaseModel):
    """transaction_change 전용 — 1차↔2차 매출/매입 거래에 랜덤 g 자동 부여."""

    side: Literal["both", "sales", "purchase"] = Field(
        "both",
        description="sales=매출(1차 판매→2차) / purchase=매입(2차 판매→1차) / both=둘 다.",
    )
    low: float = Field(0.0, ge=0.0, le=3.0, description="랜덤 g 하한 (g=1+증감율).")
    high: float = Field(1.0, ge=0.0, le=3.0, description="랜덤 g 상한 (>1=증가 허용).")
    seed: int | None = Field(None, description="재현용 난수 시드. None=비결정.")
    only_firms: list[str] | None = Field(
        None, description="일부 1차 기업 bizno 한정. None=연계된 모든 1차."
    )


class ScenarioRequest(BaseModel):
    scenario: Literal["tariff", "transaction_change", "volume"] = Field(
        ...,
        description=(
            "tariff=W불변·시드주입 / transaction_change=엣지비중 g수정 후 변화분(Δ) / "
            "volume=기업 매출/매입 m배 변동(δ=m−1)을 시드 주입·1회 전파·매출/매입 반영."
        ),
    )
    seeds: list[SeedIn] = Field(..., description="1차 기업 (bizno,upchecd,shock).")
    directions: list[Literal["upstream", "downstream"]] = Field(
        default=["upstream", "downstream"],
        min_length=1,
        description="downstream=하류/매출(매출처, 가중치 A), upstream=상류/매입(매입처, 가중치 B). 기본 둘 다. 빈 배열 금지.",
    )
    weight_a: float = Field(1.0, gt=0.0, description="매출(하류·매출처) 비중 가중치 A.")
    weight_b: float = Field(1.0, gt=0.0, description="매입(상류·매입처) 비중 가중치 B.")
    depth: int = Field(3, ge=1, le=6, description="확장 깊이.")
    trade_year: str | None = Field(None, description="거래연도 필터. None=전 연도.")
    within_subgraph: bool = Field(True, description="rate 분모: 서브그래프 내 / 전체 outgoing.")
    damping: float = Field(0.85, gt=0.0, le=1.0, description="홉당 감쇠율 α.")
    normalize: Literal["source", "counterparty"] = Field(
        "source",
        description=(
            "rate 분모 기준. source=전파 소스 기준(Σ_out≤1 수렴보장) / "
            "counterparty=거래상대 기준(경제적 매출·매입 비중 라벨, 수렴 보장 약화)."
        ),
    )
    edge_overrides: list[EdgeOverrideIn] = Field(
        default_factory=list,
        description="transaction_change 전용 — 명시 비중 수정 대상 (셀러→바이어, g).",
    )
    random_override: RandomOverrideIn | None = Field(
        None,
        description=(
            "transaction_change 전용 — 1차↔2차 매출/매입에 랜덤 g 자동 생성. "
            "지정 시 edge_overrides 대신 사용."
        ),
    )
    multipliers: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "volume 전용 — {bizno: m} 기업 전체 매출/매입 변동 배수(m=1+증감율). "
            "0.8=20%감소·1.1=10%증가. seeds 중 미지정 기업은 무변화(δ=0)."
        ),
        examples=[{"1018116406": 0.8}],
    )
    edge_multipliers: list[EdgeOverrideIn] = Field(
        default_factory=list,
        description=(
            "volume 전용 — 특정 거래(엣지)만 변동. (from→to) 거래가 g배, 파트너(to)에 "
            "그 거래 비중만큼만 반영(share×(g−1)). 허브 자신은 불변. factor=g(1+증감율)."
        ),
    )
    pin_seeds: bool = Field(
        True,
        description=(
            "volume 전용 — True(기본): 시드를 입력값에 고정(되돌이 차단, 시드=정확히 m). "
            "False: 순환 피드백 허용(시드도 증폭, 일반균형 총효과)."
        ),
    )
    industry_code: list[str] = Field(
        default=["전체"],
        min_length=1,
        description=(
            "산업 필터(HS chapter prefix-2). 시드+확장 노드를 선택 산업의 HS 거래구성을 "
            "가진 기업으로 한정(미분류 제외). 가능: 전체 / 농산물(01~24) / 에너지(27) / "
            "화학(28~39) / 철강·금속(72~83) / 기계(84) / 전자·반도체(85) / 자동차(87) / "
            "섬유·의류(50~63). 여러 개 가능. '전체' 포함 시 필터 없음."
        ),
        examples=[["전체"], ["화학", "철강/금속"]],
    )


class DirectionResultOut(BaseModel):
    direction: str = Field(..., description="upstream | downstream.")
    effect_label: str = Field(..., description="매출 파급 | 매입 파급.")
    weight: float = Field(..., description="적용된 가중치 A 또는 B.")
    shock_list: list[ShockRowOut] = Field(
        ..., description="tariff=누적 파급, transaction_change=노드별 변화분 Δ."
    )
    total_shock: float
    iterations: int
    converged: bool
    n_nodes: int = Field(..., description="조립된 서브그래프 노드 수.")
    n_edges: int = Field(..., description="조립된 (방향·가중치 반영) 엣지 수.")


class ScenarioResponse(BaseModel):
    scenario: str
    directions: list[DirectionResultOut]
    warnings: list[str]
    applied_overrides: list[EdgeOverrideIn] = Field(
        default_factory=list,
        description="실제 적용된 거래변화 g (랜덤 생성 포함). 재현·표시용.",
    )


# ─── 엔드포인트 ───────────────────────────────────────────────────────────


_COMMON_RESPONSES: dict = {
    503: {"description": "PostgreSQL 또는 LLM 백엔드 도달 실패."},
}


@router.post(
    "/select_primary",
    response_model=SelectPrimaryResponse,
    summary="[2단계] HS → ra603 거래구성 → 1차 기업 선별",
    responses=_COMMON_RESPONSES,
)
def select_primary(req: SelectPrimaryRequest) -> SelectPrimaryResponse:
    try:
        res = _select_primary_firms(
            req.hscode,
            year=req.year,
            exim=req.exim,
            top_k=req.top_k,
            min_ratio=req.min_ratio,
            ratio_weight=req.ratio_weight,
            tier_weight=req.tier_weight,
        )
    except SQLAlchemyError as exc:
        log.exception("select_primary db error")
        raise HTTPException(
            status_code=503, detail=f"db unreachable: {exc.__class__.__name__}"
        ) from exc
    return SelectPrimaryResponse(
        hscode=res.hscode,
        hs_digits=res.hs_digits,
        year=res.year,
        exim=res.exim,
        firms=[FirmOut(**vars(f)) for f in res.firms],
    )


@router.post(
    "/assemble",
    response_model=AssembleResponse,
    summary="[3단계] 시드 → 3depth 확장 → 전파 입력 조립 (복합키)",
    responses=_COMMON_RESPONSES,
)
def assemble(req: AssembleRequest) -> AssembleResponse:
    seeds = [(s.bizno, s.upchecd) for s in req.seeds]
    shock_map = {s.bizno: s.shock for s in req.seeds}
    try:
        asm = _assemble(
            seeds,
            depth=req.depth,
            trade_year=req.trade_year,
            within_subgraph=req.within_subgraph,
            damping=req.damping,
            seed_shock=shock_map,
        )
    except SQLAlchemyError as exc:
        log.exception("assemble db error")
        raise HTTPException(
            status_code=503, detail=f"db unreachable: {exc.__class__.__name__}"
        ) from exc
    return AssembleResponse(
        edges=[AssembledEdgeOut(**e) for e in asm.edges],
        init_sub_graph=asm.init_sub_graph,
        nodes=[AssembledNodeOut(**vars(n)) for n in asm.nodes],
        depth=asm.depth,
        rate_kind=asm.rate_kind,
        within_subgraph=asm.within_subgraph,
        damping=asm.damping,
        warnings=asm.warnings,
    )


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
        edges=[e.model_dump() for e in req.edges],
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


@router.post(
    "/scenario",
    response_model=ScenarioResponse,
    summary="관세 충격 / 거래 변화 — 방향(매출·매입)별 전파 (단일 알고리즘 래퍼)",
    responses=_COMMON_RESPONSES,
)
def scenario(req: ScenarioRequest) -> ScenarioResponse:
    seeds = [(s.bizno, s.upchecd) for s in req.seeds]
    shock_map = {s.bizno: s.shock for s in req.seeds}
    random_spec = None
    if req.random_override is not None:
        ro = req.random_override
        random_spec = _RandomOverrideSpec(
            side=ro.side,
            low=ro.low,
            high=ro.high,
            seed=ro.seed,
            only_firms=tuple(ro.only_firms) if ro.only_firms else None,
        )
    edge_overrides = {(o.from_bizno, o.to_bizno): o.factor for o in req.edge_overrides} or None
    try:
        sres = _run_scenario(
            req.scenario,
            seeds,
            weight_a=req.weight_a,
            weight_b=req.weight_b,
            directions=req.directions,
            depth=req.depth,
            trade_year=req.trade_year,
            within_subgraph=req.within_subgraph,
            damping=req.damping,
            normalize=req.normalize,
            seed_shock=shock_map,
            edge_overrides=edge_overrides,
            random_spec=random_spec,
            multipliers=req.multipliers,
            edge_multipliers={
                (o.from_bizno, o.to_bizno): o.factor for o in req.edge_multipliers
            },
            pin_seeds=req.pin_seeds,
            industry_code=req.industry_code,
        )
    except ValueError as exc:
        # 빈 edge_overrides·only_firms 무교집합 등 입력 오류 → 422
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        log.exception("scenario db error")
        raise HTTPException(
            status_code=503, detail=f"db unreachable: {exc.__class__.__name__}"
        ) from exc
    return ScenarioResponse(
        applied_overrides=[
            EdgeOverrideIn(from_bizno=s, to_bizno=b, factor=g)
            for (s, b), g in sorted(sres.applied_overrides.items())
        ],
        scenario=sres.scenario,
        directions=[
            DirectionResultOut(
                direction=d.direction,
                effect_label=d.effect_label,
                weight=d.weight,
                shock_list=[ShockRowOut(**r) for r in d.result.shock_list],
                total_shock=d.result.total_shock,
                iterations=d.result.iterations,
                converged=d.result.converged,
                n_nodes=len(d.assembled.nodes),
                n_edges=len(d.assembled.edges),
            )
            for d in sres.directions
        ],
        warnings=sres.warnings,
    )
