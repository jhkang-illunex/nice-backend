"""nice_shock API 서버 — 순수 쇼크 전파 (DB·LLM 의존 없음).

엔드포인트
  POST /api/shock/tariff  : 관세(외생) 충격
  POST /api/shock/volume  : 거래량 변동
  GET  /health

입력 그래프(triple_list)를 클라이언트가 제공하므로 stateless — 수평 확장 자유.
"""
from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from nice_shock.engine import propagate_dispatch
from nice_shock.scenario import run_tariff, run_volume

app = FastAPI(
    title="NICE shock-propagate",
    version="1.0.0",
    description="triple_list 입력 순수 쇼크 전파 — 관세충격 / 거래량변동.",
)

# ── Swagger 예제 (a·b·c 그래프, b에 충격 1 → 등비급수 수렴) ───────────────────
_EX_EDGES = [
    {"from": "a", "to": "b", "rate": 0.115},
    {"from": "b", "to": "a", "rate": 0.060},
    {"from": "c", "to": "b", "rate": 0.912},
    {"from": "b", "to": "c", "rate": 0.607},
]
_EX_TARIFF_REQ = {
    "triple_list": _EX_EDGES, "seed_list": ["b"], "shock_rate": 1.0, "direction": "export",
}
_EX_VOLUME_REQ = {
    "triple_list": [{"from": "a", "to": "b", "rate": 0.115}, {"from": "b", "to": "c", "rate": 0.607}],
    "seed_list": ["b"], "node_overrides": [{"p1": "b", "w1": 0.8}], "direction": "export",
}
_EX_PROPAGATE_REQ = {"triple_list": [{"from": "a", "to": "b", "rate": 0.5}], "init": {"a": 1.0}}
# tariff·volume 응답 (외부) — 간소화: direction(import|export)·total_shock·data_list 만.
# (pin_seeds=False 기준: 시드 b 의 자기순환 되먹임 포함 전파 → b 가 1.0 이상으로 증폭.)
_EX_DATA_RESP = {
    "direction": "export", "total_shock": 3.79281,
    "data_list": [
        {"node_id": "b", "shock": 2.27523, "depth": 1},
        {"node_id": "c", "shock": 1.38106, "depth": 2},
        {"node_id": "a", "shock": 0.13651, "depth": 2},
    ],
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

# direction 입력/출력 — import=매입(upstream,1) / export=매출(downstream,0).
Direction = Literal["import", "export"]
_DIR_TO_INT = {"import": 1, "export": 0}
_INT_TO_DIR = {1: "import", 0: "export"}


# ── 외부 응답 (간소화) ───────────────────────────────────────────────────────
class NodeOut(BaseModel):
    node_id: str
    shock: float
    depth: int | None = Field(None, description="시드=1, 시드에서 홉당 +1 (도달 못하면 null)")


class DataResponse(BaseModel):
    direction: Direction = Field(..., description="import=매입 / export=매출 (입력 echo)")
    total_shock: float
    data_list: list[NodeOut]

    model_config = {"json_schema_extra": {"example": _EX_DATA_RESP}}


def _to_data_response(dr) -> DataResponse:
    r = dr["result"]
    depths = dr.get("depths", {})
    return DataResponse(
        direction=_INT_TO_DIR[dr["direction"]],
        total_shock=r.total_shock,
        data_list=[
            NodeOut(node_id=row["bizno"], shock=row["shock"], depth=depths.get(row["bizno"]))
            for row in r.shock_list
        ],
    )


# ── 관세(외생) 충격 ────────────────────────────────────────────────────────
# 내부 고정값(tariff·volume 공통) — pin_seeds/method/cycle_damping 은 외부 노출 안 함.
# pin_seeds=False (NICE 이사님 확정, 2026-06-24): 시드도 incoming 엣지를 그대로 두어 자기
#   순환 되먹임을 포함한 전파를 계산한다(시드를 주입값에 고정하지 않음). 발산 순환은
#   cycle_damping(조건부)으로 처리.
_DEFAULT_PIN_SEEDS = False
_DEFAULT_METHOD = "scc"
_DEFAULT_CYCLE_DAMPING = 0.95


class TariffRequest(BaseModel):
    triple_list: list[TripleIn] = Field(..., description="거래쌍·거래비율 엣지 목록")
    seed_list: list[str] = Field(..., description="외생충격 받는 1차 기업 node_id")
    shock_rate: float = Field(..., description="시드 주입 충격량 (음수 가능)")
    direction: Direction = Field("import", description="import=매입(기본) / export=매출")

    model_config = {"json_schema_extra": {"example": _EX_TARIFF_REQ}}


@app.post("/api/shock/tariff", response_model=DataResponse, summary="관세(외생) 충격")
def tariff(req: TariffRequest) -> DataResponse:
    results = run_tariff(
        [t.model_dump(by_alias=True) for t in req.triple_list],
        req.seed_list,
        req.shock_rate,
        [_DIR_TO_INT[req.direction]],
        pin_seeds=_DEFAULT_PIN_SEEDS,
        method=_DEFAULT_METHOD,
        cycle_damping=_DEFAULT_CYCLE_DAMPING,
    )
    return _to_data_response(results[0])


# ── 거래량 변동 ────────────────────────────────────────────────────────────
class OverrideIn(BaseModel):
    p1: str = Field(..., description="변동 대상 기업 node_id")
    w1: float = Field(..., description="factor = 1+증감율 (0.8=−20%)")


class VolumeRequest(BaseModel):
    triple_list: list[TripleIn]
    seed_list: list[str]
    node_overrides: list[OverrideIn] = Field(..., description="[{p1,w1}] 노드별 거래량 factor")
    direction: Direction = Field("import", description="import=매입(기본) / export=매출")

    model_config = {"json_schema_extra": {"example": _EX_VOLUME_REQ}}


@app.post("/api/shock/volume", response_model=DataResponse, summary="거래량 변동")
def volume(req: VolumeRequest) -> DataResponse:
    # pin_seeds/method/cycle_damping 은 내부 고정 (tariff 와 동일 정책).
    results = run_volume(
        [t.model_dump(by_alias=True) for t in req.triple_list],
        req.seed_list,
        [o.model_dump() for o in req.node_overrides],
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


@app.get("/health", summary="헬스체크")
def health() -> dict:
    return {"status": "ok", "service": "shock-propagate"}
