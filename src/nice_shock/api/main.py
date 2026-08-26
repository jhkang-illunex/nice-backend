"""nice_shock API 서버 — 순수 쇼크 전파 (DB·LLM 의존 없음).

엔드포인트 ("1차 기업 충격 금액 산출 로직 v2" — docs/충격금액_산출_로직_v2.pdf)
  POST /api/shock/tariff  : 수출입(외생) 충격 — 시드별 주입액
                            = total_amount(손익계산서 매출액, 인자)
                            × Σrate(HS10 품목별 수출입 비중 — bse_yr 기준으로 backend
                              POST /trade/weight 일괄 조회, 각 0~1, iokind 방향분만)
                            × shock_rate(충격 비율, 인자 — 유한 실수, NaN/inf 만 거부)
                            ※ backend 가 H10 단일 계층만 반환하고 요청 내 중복 코드는
                            dedup 하므로 이중계상(중복/prefix) 여지 없음.
  POST /api/shock/volume  : 거래량 변동(국내 충격) — 시드별 주입액
                            = total_amount(총매출) × shock_rate (기업별 인자 — 유한 실수)
  GET  /health

입력 그래프(triple_list)를 클라이언트가 제공하므로 stateless — 수평 확장 자유.
(tariff 만 backend /trade/weight API 에 의존 — 계약은 nice_shock.rate_client 참고.)
"""
from __future__ import annotations

import logging
import math
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from nice_shock.cri import compute_cri
from nice_shock.engine import propagate_dispatch
from nice_shock.rate_client import (
    EXIM_EXPORT,
    EXIM_IMPORT,
    RateApiUnavailable,
    fetch_weights,
)
from nice_shock.scenario import run_tariff, run_volume

log = logging.getLogger(__name__)

app = FastAPI(
    title="NICE shock-propagate",
    version="1.0.0",
    description="triple_list 입력 순수 쇼크 전파 — 관세충격 / 거래량변동.",
)


def _finite_safe(o):
    """422 detail 안의 비유한 float(NaN/±inf)를 문자열로 치환 — strict JSON 직렬화 보호."""
    if isinstance(o, float) and not math.isfinite(o):
        return str(o)
    if isinstance(o, dict):
        return {k: _finite_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite_safe(v) for v in o]
    return o


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 기본 핸들러와 동일 형상({"detail": errors}) 유지. 단 NaN/inf 입력이 거부될 때
    # 오류 detail 에 echo 되는 원본 값이 strict JSON 직렬화를 깨며 422 대신 500 이
    # 나던 문제를 막기 위해 비유한 float 만 문자열로 치환한다.
    return JSONResponse(
        status_code=422, content={"detail": _finite_safe(jsonable_encoder(exc.errors()))}
    )

# ── Swagger 예제 (a·b·c 그래프, b에 충격 1 → 등비급수 수렴) ───────────────────
_EX_EDGES = [
    {"from": "a", "to": "b", "rate": 0.115},
    {"from": "b", "to": "a", "rate": 0.060},
    {"from": "c", "to": "b", "rate": 0.912},
    {"from": "b", "to": "c", "rate": 0.607},
]
_EX_TARIFF_REQ = {
    "triple_list": _EX_EDGES,
    "bse_yr": "2025",
    "shock_rate": 0.1,
    "seed_list": [
        {
            "seed_id": "b", "upche_cd": "184084", "total_amount": 10000000.0,
            "hscodes": ["3801300000", "3901100000"],
        }
    ],
    "direction": "export",
    "iokind": "out",
}
_EX_VOLUME_REQ = {
    "triple_list": [{"from": "a", "to": "b", "rate": 0.115}, {"from": "b", "to": "c", "rate": 0.607}],
    "seed_list": [{"seed_id": "b", "total_amount": 1000000.0, "shock_rate": 0.2}],
    "direction": "export",
}
_EX_PROPAGATE_REQ = {"triple_list": [{"from": "a", "to": "b", "rate": 0.5}], "init": {"a": 1.0}}
# tariff·volume 응답 (외부) — 간소화: direction(import|export)·total_shock·data_list 만.
# (pin_seeds=False 기준: 시드 b 주입 100만원 = total_amount 1천만 × Σrate 1.0(조회 가정) ×
#  shock_rate 0.1 — 자기순환 되먹임 포함 전파로 b 가 주입액 이상으로 증폭.)
_EX_DATA_RESP = {
    "direction": "export", "total_shock": 3792808.45,
    "data_list": [
        {"node_id": "b", "shock": 2275230.03, "depth": 1},
        {"node_id": "c", "shock": 1381064.63, "depth": 2},
        {"node_id": "a", "shock": 136513.80, "depth": 2},
    ],
    "excluded_seeds": [],
}
# /propagate 응답 (내부 저수준) — 진단값 포함 유지.
_EX_DIRECTION_OUT = {
    "direction": -1, "converged": True, "iterations": 0, "total_shock": 1.5,
    "shock_list": [
        {"bizno": "a", "shock": 1.0, "depth": None},
        {"bizno": "b", "shock": 0.5, "depth": None},
    ],
    "damped_cycles": [],
}


# ── 공통 스키마 ────────────────────────────────────────────────────────────
class TripleIn(BaseModel):
    from_: str = Field(..., alias="from", description="셀러(거래 출발) bizno")
    to: str = Field(..., description="바이어(거래 도착) bizno")
    rate: float = Field(..., description="거래비율(trade_rate, source 정규화)")

    model_config = {"populate_by_name": True}


class ShockRowOut(BaseModel):
    bizno: str
    shock: float
    depth: int | None = Field(None, description="시드=1, 시드에서 홉당 +1 (도달 못하면 null)")


class DampedCycleOut(BaseModel):
    members: list[str]
    rho: float
    factor: float
    rho_after: float


class DirectionOut(BaseModel):
    direction: int = Field(..., description="0=매출 파급(downstream) / 1=매입 파급(upstream)")
    converged: bool
    iterations: int
    total_shock: float
    shock_list: list[ShockRowOut]
    damped_cycles: list[DampedCycleOut] = []

    model_config = {"json_schema_extra": {"example": _EX_DIRECTION_OUT}}


_METHODS = ("scc", "iterative")

# direction 입력/출력 — import=매입(upstream,1) / export=매출(downstream,0). 전파 방향 전용.
Direction = Literal["import", "export"]
_DIR_TO_INT = {"import": 1, "export": 0}
_INT_TO_DIR = {1: "import", 0: "export"}
# iokind — tariff rate 조회 방향. backend tseximdivcd(0=전체수출/3=전체수입)와 매핑.
# (2026-08-25 개편: rate 조회 방향을 direction 에서 분리 — direction 은 전파 방향만 담당.)
IoKind = Literal["in", "out"]
_IOKIND_TO_EXIM = {"in": EXIM_IMPORT, "out": EXIM_EXPORT}


# ── 외부 응답 (간소화) ───────────────────────────────────────────────────────
class NodeOut(BaseModel):
    node_id: str
    shock: float
    depth: int | None = Field(None, description="시드=1, 시드에서 홉당 +1 (도달 못하면 null)")


class ExcludedSeedOut(BaseModel):
    node_id: str = Field(..., description="제외된 시드 seed_id(사업자번호)")
    reason: str = Field(..., description="제외 사유 — 그래프(from∪to) 미포함 / rate 조회 실패 등")


_GRAPH_MISS_REASON = "triple_list 노드 집합(from∪to)에 없음"


class DataResponse(BaseModel):
    direction: Direction = Field(..., description="import=매입 / export=매출 (입력 echo)")
    total_shock: float
    data_list: list[NodeOut]
    excluded_seeds: list[ExcludedSeedOut] = Field(
        default_factory=list,
        description="전파에서 제외된 시드와 사유 (init·depth·total_shock 미포함). "
        "비어 있지 않으면 시드/그래프 조립 불일치 신호.",
    )

    model_config = {"json_schema_extra": {"example": _EX_DATA_RESP}}


def _to_data_response(dr, pre_excluded: list[dict] | None = None) -> DataResponse:
    r = dr["result"]
    depths = dr.get("depths", {})
    excluded = list(pre_excluded or []) + [
        {"node_id": nid, "reason": _GRAPH_MISS_REASON} for nid in dr.get("excluded", [])
    ]
    return DataResponse(
        direction=_INT_TO_DIR[dr["direction"]],
        total_shock=r.total_shock,
        data_list=[
            NodeOut(node_id=row["bizno"], shock=row["shock"], depth=depths.get(row["bizno"]))
            for row in r.shock_list
        ],
        excluded_seeds=[ExcludedSeedOut(**e) for e in excluded],
    )


# ── 관세(외생) 충격 ────────────────────────────────────────────────────────
# 내부 고정값(tariff·volume 공통) — pin_seeds/method/cycle_damping 은 외부 노출 안 함.
# pin_seeds=False (NICE 이사님 확정, 2026-06-24): 시드도 incoming 엣지를 그대로 두어 자기
#   순환 되먹임을 포함한 전파를 계산한다(시드를 주입값에 고정하지 않음). 발산 순환은
#   cycle_damping(조건부)으로 처리.
_DEFAULT_PIN_SEEDS = False
_DEFAULT_METHOD = "scc"
_DEFAULT_CYCLE_DAMPING = 0.95


class TariffSeedIn(BaseModel):
    seed_id: str = Field(
        ..., description="기업 사업자등록번호(bizno) — triple_list 의 node_id 와 동일 체계"
    )
    upche_cd: str = Field(
        ..., description="업체코드 — rate(수출입 비중) 조회 키 (hscode 와 함께 사용)"
    )
    total_amount: float = Field(
        ..., ge=0.0, description="이 기업의 총매출(원) — 손익계산서(ab01·ac01) 매출액"
    )
    hscodes: list[Annotated[str, Field(pattern=r"^\d{10}$")]] = Field(
        ...,
        min_length=1,
        description="품목코드 목록 (HS 10자리 digit) — backend /trade/weight 일괄 조회로 "
        "품목별 수출(입) 비중을 받아 **합산**(Σ). 중복 코드는 dedup 후 합산하고 backend 가 "
        "H10 단일 계층만 반환하므로 이중계상 여지 없음. 실적 없는 품목은 비중 0 취급, "
        "전 품목 실적 없음이면 시드 excluded.",
    )


class TariffRequest(BaseModel):
    triple_list: list[TripleIn] = Field(..., description="거래쌍·거래비율 엣지 목록")
    bse_yr: str = Field(
        "2025",
        pattern=r"^\d{4}$",
        description="기준 연도 — 수출입 비중(/trade/weight) 조회 기준. 기본 2025 "
        "(2026 데이터 불충분).",
    )
    shock_rate: float = Field(
        ...,
        allow_inf_nan=False,
        description="충격 비율 (전 시드 공통). 유한 실수면 제한 없음 — 음수(완화)·1 초과 "
        "허용, NaN/±inf 만 422. 시드별 주입액 = total_amount(손익계산서 매출액) × "
        "Σrate(backend 조회, 각 0~1) × shock_rate",
    )
    seed_list: list[TariffSeedIn] = Field(
        ...,
        description="외생충격 받는 1차 기업 [{seed_id, upche_cd, total_amount, hscodes}] — "
        "rate 는 전 시드의 (upche_cd × hscodes) 를 backend API(RATE_API_URL, "
        "POST /trade/weight) 로 **일괄 조회**",
    )
    direction: Direction = Field(
        "import",
        description="전파 방향 — import=매입(기본, 상류) / export=매출(하류). "
        "rate 조회 방향은 iokind 가 별도로 지정",
    )
    iokind: IoKind = Field(
        "in",
        description="rate(수출입 비중) 조회 방향 — in=수입(tseximdivcd '3', 기본) / "
        "out=수출('0'). backend /trade/weight 응답에서 이 방향의 행만 Σrate 합산에 사용",
    )

    model_config = {"json_schema_extra": {"example": _EX_TARIFF_REQ}}


@app.post(
    "/api/shock/tariff",
    response_model=DataResponse,
    summary="관세(외생) 충격",
    responses={503: {"description": "backend rate API 미설정 또는 도달 불가 (RATE_API_URL)"}},
)
def tariff(req: TariffRequest) -> DataResponse:
    # 전 시드의 (upche_cd × hscodes) 를 backend /trade/weight 로 한 번에 조회한 뒤
    # 시드별 Σrate 를 만든다. 응답에 행이 없는 (업체, 품목) 셀 = 그 품목 실적 없음
    # = 비중 0 취급(부분 합산). 전 품목 실적 없음일 때만 시드 excluded.
    exim = _IOKIND_TO_EXIM[req.iokind]
    upchecds = sorted({s.upche_cd for s in req.seed_list})
    hscodes = sorted({h for s in req.seed_list for h in s.hscodes})
    try:
        weights = (
            fetch_weights(req.bse_yr, upchecds, hscodes, exim=exim) if req.seed_list else {}
        )
    except RateApiUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    seeds: list[dict] = []
    pre_excluded: list[dict] = []
    for s in req.seed_list:
        codes = list(dict.fromkeys(s.hscodes))  # 요청 내 중복 코드 dedup (이중계상 방지)
        found = {h: weights[(s.upche_cd, h)] for h in codes if (s.upche_cd, h) in weights}
        if not found:
            pre_excluded.append({
                "node_id": s.seed_id,
                "reason": f"기준연도 {req.bse_yr} 전 품목({len(codes)}건) 수출입 실적 없음 "
                f"(/trade/weight 비중 부재, upche_cd={s.upche_cd})",
            })
            continue
        if len(found) < len(codes):
            missing = [h for h in codes if h not in found]
            log.warning("tariff seed %s: %d/%d hscode 실적 없음 — 부분 합산: %s",
                        s.seed_id, len(missing), len(codes), ", ".join(missing))
        rate_sum = sum(found.values())
        seeds.append(
            {"node_id": s.seed_id, "shock_amount": s.total_amount * rate_sum * req.shock_rate}
        )
    results = run_tariff(
        [t.model_dump(by_alias=True) for t in req.triple_list],
        seeds,
        [_DIR_TO_INT[req.direction]],
        pin_seeds=_DEFAULT_PIN_SEEDS,
        method=_DEFAULT_METHOD,
        cycle_damping=_DEFAULT_CYCLE_DAMPING,
    )
    return _to_data_response(results[0], pre_excluded)


# ── 거래량 변동 ────────────────────────────────────────────────────────────
class VolumeSeedIn(BaseModel):
    seed_id: str = Field(
        ..., description="기업 사업자등록번호(bizno) — triple_list 의 node_id 와 동일 체계"
    )
    total_amount: float = Field(
        ..., ge=0.0,
        description="이 기업의 총매출(원) — 손익계산서(ab01·ac01) 매출액 (매입 기준이면 매입 총액)"
    )
    shock_rate: float = Field(
        ...,
        allow_inf_nan=False,
        description="이 기업의 충격 비율. 유한 실수면 제한 없음 — 음수(감소분)·1 초과 "
        "허용, NaN/±inf 만 422 (0=무변화, 예: 0.2=20%). "
        "주입액(충격 금액) = total_amount(총매출) × shock_rate",
    )


class VolumeRequest(BaseModel):
    triple_list: list[TripleIn]
    seed_list: list[VolumeSeedIn] = Field(
        ...,
        description="변동 대상 1차 기업 [{seed_id, total_amount, shock_rate}] — "
        "기업별 변동 비율을 개별 입력",
    )
    direction: Direction = Field("import", description="전파 방향 — import=매입(기본) / export=매출")
    iokind: IoKind = Field(
        "in",
        description="예약 인자(현재 미사용) — tariff 와 인자 통일 목적. in(기본)/out. "
        "DB 구조 검토 후 사용 여부 확정 예정",
    )

    model_config = {"json_schema_extra": {"example": _EX_VOLUME_REQ}}


@app.post(
    "/api/shock/volume",
    response_model=DataResponse,
    summary="거래량 변동",
    description=(
        "국내 거래량 변동 — 시드별 주입액 = total_amount × shock_rate(기업별). "
        "**기준연도(bse_yr) 개념이 없다**: tariff 와 달리 backend 비중 조회가 없어 "
        "연도 인자를 받지 않으며, total_amount(재무 기준연도)와 triple_list(거래 "
        "기준연도)의 연도 정합은 호출자 책임이다."
    ),
)
def volume(req: VolumeRequest) -> DataResponse:
    # pin_seeds/method/cycle_damping 은 내부 고정 (tariff 와 동일 정책).
    seeds = [
        {"node_id": s.seed_id, "shock_amount": s.total_amount * s.shock_rate}
        for s in req.seed_list
    ]
    results = run_volume(
        [t.model_dump(by_alias=True) for t in req.triple_list],
        seeds,
        [_DIR_TO_INT[req.direction]],
        pin_seeds=_DEFAULT_PIN_SEEDS,
        method=_DEFAULT_METHOD,
        cycle_damping=_DEFAULT_CYCLE_DAMPING,
    )
    return _to_data_response(results[0])


# ── 저수준 전파 (이미 조립·정향된 edges + 노드별 init 직접) ─────────────────
class PropagateRequest(BaseModel):
    """edges 와 init 을 그대로 받는 저수준 전파.

    triple_list 는 이미 전파 방향으로 정향·정규화된 엣지(클라이언트가 조립).
    init 은 노드별 초기값({node: shock/δ}). pin·방향·δ 산출은 호출자(nice_dbtool) 책임.
    """
    triple_list: list[TripleIn]
    init: dict[str, float] = Field(..., description="노드별 초기값 {bizno: shock 또는 δ}")
    method: str = "scc"
    cycle_damping: float = Field(0.95, gt=0.0, lt=1.0)

    model_config = {"json_schema_extra": {"example": _EX_PROPAGATE_REQ}}


@app.post("/api/shock/propagate", response_model=DirectionOut, summary="저수준 전파(edges+init)")
def propagate(req: PropagateRequest) -> DirectionOut:
    edges = [{"from_bizno": t.from_, "to_bizno": t.to, "rate": t.rate} for t in req.triple_list]
    res = propagate_dispatch(
        edges=edges, init_sub_graph=req.init,
        method=req.method if req.method in _METHODS else "scc",
        cycle_damping=req.cycle_damping,
    )
    return DirectionOut(
        direction=-1, converged=res.converged, iterations=res.iterations,
        total_shock=res.total_shock,
        shock_list=[ShockRowOut(**row) for row in res.shock_list],
        damped_cycles=[DampedCycleOut(**d) for d in res.damped_cycles],
    )


# ── CRI(신용위험지표) — 판매/구매망 신용등급 가중평균 (DB 의존 없음) ─────────────
# 누적 거래망 T=Σ_k λ^k W^k (직접+간접+loop) 위에서 거래상대 신용등급을 거래비중 가중평균.
# ⚠️ 현재 외부 비노출(2026-08-25 결정): 아래 @app.post 데코레이터를 주석 처리해 라우트
#    미등록 상태 — /api/cri 호출 시 404. 스키마·계산 함수는 보존, 재노출 시 주석만 해제.
_EX_CRI_REQ = {
    "nodes": [
        {"id": "A", "grade": "AA", "sales": 1000},
        {"id": "B", "grade": "NR", "sales": 800},
        {"id": "C", "grade": "BBB", "sales": 500},
        {"id": "D", "grade": "A", "sales": 600},
        {"id": "E", "grade": "BB", "sales": 400},
    ],
    "edges": [
        {"source": "A", "target": "B", "sell_share": 0.300, "buy_share": 0.375},
        {"source": "A", "target": "D", "sell_share": 0.200, "buy_share": 0.333},
        {"source": "D", "target": "B", "sell_share": 0.300, "buy_share": 0.225},
        {"source": "D", "target": "E", "sell_share": 0.400, "buy_share": 0.600},
        {"source": "B", "target": "C", "sell_share": 0.500, "buy_share": 0.800},
        {"source": "B", "target": "A", "sell_share": 0.200, "buy_share": 0.160},
    ],
}
_EX_CRI_RESP = {
    "data_list": [
        {"id": "A",
         "sell": {"total_weight": 0.883621, "valid_weight": 0.495690, "coverage": 0.560976,
                  "avg_cri": 3.739130, "exposure": 1.853448},
         "buy": {"total_weight": 0.211204, "valid_weight": 0.038793, "coverage": 0.183673,
                 "avg_cri": 3.0, "exposure": 0.116378}},
        {"id": "C",  # 판매 엣지 없음 → 판매망 지표 null
         "sell": {"total_weight": 0.0, "valid_weight": 0.0, "coverage": None,
                  "avg_cri": None, "exposure": 0.0},
         "buy": {"total_weight": 1.443882, "valid_weight": 0.581824, "coverage": 0.402958,
                 "avg_cri": 2.333370, "exposure": 1.357612}},
    ],
    "network": {
        "sell": {"risk_index": 3.784242, "coverage": 0.723983},
        "buy": {"risk_index": 2.393419, "coverage": 0.690818},
    },
}


class CriNodeIn(BaseModel):
    id: str = Field(..., description="노드(기업) 식별자")
    grade: str | None = Field(
        None, description="신용등급(AAA~D, NR 등; 노치 포함 가능). score 미지정 시 이걸로 매핑")
    score: int | None = Field(
        None, description="등급점수(1=AAA … 10=D) 직접 지정 — 주면 grade 무시. 무등급은 생략")
    sales: float = Field(..., description="매출액 (Network Risk Index 가중에만 사용)")


class CriEdgeIn(BaseModel):
    source: str = Field(..., description="셀러(판매) node id")
    target: str = Field(..., description="바이어(구매) node id")
    sell_share: float = Field(..., description="판매비중 = 거래액 / 셀러 매출")
    buy_share: float = Field(..., description="구매비중 = 거래액 / 바이어 매출")


class CriRequest(BaseModel):
    nodes: list[CriNodeIn] = Field(..., description="노드(기업)별 등급·매출")
    edges: list[CriEdgeIn] = Field(..., description="거래 엣지(셀러→바이어)·판매/구매 비중")
    lamb: float = Field(1.0, gt=0.0, le=1.0, description="단계 감쇠 λ (기본 1.0=무감쇠)")

    model_config = {"json_schema_extra": {"example": _EX_CRI_REQ}}


class CriMetricsOut(BaseModel):
    total_weight: float = Field(..., description="전체 누적 거래비중(self 제외)")
    valid_weight: float = Field(..., description="유효(등급보유 거래처) 누적 거래비중")
    coverage: float | None = Field(None, description="유효/전체 — 등급 평가 가능 비율(거래처 없으면 null)")
    avg_cri: float | None = Field(None, description="가중평균 CRI 점수(클수록 위험, 유효 없으면 null)")
    exposure: float = Field(..., description="CRI Exposure = Σ(누적비중×등급점수)")


class CriNodeOut(BaseModel):
    id: str
    sell: CriMetricsOut = Field(..., description="판매망(셀러 관점) 지표")
    buy: CriMetricsOut = Field(..., description="구매망(바이어 관점) 지표")


class CriNetworkSideOut(BaseModel):
    risk_index: float | None = Field(None, description="Network Risk Index(매출가중 평균 거래상대 CRI)")
    coverage: float | None = Field(None, description="Network Coverage(매출가중 유효/전체)")


class CriNetworkOut(BaseModel):
    sell: CriNetworkSideOut
    buy: CriNetworkSideOut


class CriResponse(BaseModel):
    data_list: list[CriNodeOut] = Field(..., description="노드별 판매망/구매망 지표")
    network: CriNetworkOut = Field(..., description="네트워크 전체 지표")

    model_config = {"json_schema_extra": {"example": _EX_CRI_RESP}}


# @app.post("/api/cri", response_model=CriResponse, summary="CRI(신용위험지표) 판매/구매망")
def cri(req: CriRequest) -> CriResponse:
    res = compute_cri(
        [n.model_dump() for n in req.nodes],
        [e.model_dump() for e in req.edges],
        lamb=req.lamb,
    )
    data_list = [
        CriNodeOut(id=nid, sell=CriMetricsOut(**m["sell"]), buy=CriMetricsOut(**m["buy"]))
        for nid, m in res["nodes"].items()
    ]
    net = res["network"]
    return CriResponse(
        data_list=data_list,
        network=CriNetworkOut(
            sell=CriNetworkSideOut(**net["sell"]),
            buy=CriNetworkSideOut(**net["buy"]),
        ),
    )


@app.get("/health", summary="헬스체크")
def health() -> dict:
    return {"status": "ok", "service": "shock-propagate"}
