"""nice_shock API 서버 — 순수 쇼크 전파 (DB·LLM 의존 없음).

엔드포인트
  POST /api/shock/tariff  : 관세(외생) 충격
  POST /api/shock/volume  : 거래량 변동
  GET  /health

입력 그래프(triple_list)를 클라이언트가 제공하므로 stateless — 수평 확장 자유.
"""
from __future__ import annotations

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
    "triple_list": _EX_EDGES, "seed_list": ["b"], "shock_rate": 1.0, "directions": [0],
}
_EX_VOLUME_REQ = {
    "triple_list": [{"from": "a", "to": "b", "rate": 0.115}, {"from": "b", "to": "c", "rate": 0.607}],
    "seed_list": ["b"], "edge_overrides": [{"p1": "b", "w1": 0.8}], "directions": [0],
}
_EX_PROPAGATE_REQ = {"triple_list": [{"from": "a", "to": "b", "rate": 0.5}], "init": {"a": 1.0}}
_EX_SCENARIO_RESP = {
    "directions": [{
        "direction": 0, "converged": True, "iterations": 0, "total_shock": 3.79281,
        "shock_list": [
            {"bizno": "b", "shock": 2.27523, "depth": 1},
            {"bizno": "c", "shock": 1.38106, "depth": 2},
            {"bizno": "a", "shock": 0.13651, "depth": 2},
        ],
        "damped_cycles": [],
    }],
}
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


class ScenarioResponse(BaseModel):
    directions: list[DirectionOut]

    model_config = {"json_schema_extra": {"example": _EX_SCENARIO_RESP}}


_METHODS = ("scc", "iterative")


def _to_out(results) -> ScenarioResponse:
    dirs = []
    for dr in results:
        r = dr["result"]
        depths = dr.get("depths", {})
        dirs.append(
            DirectionOut(
                direction=dr["direction"],
                converged=r.converged,
                iterations=r.iterations,
                total_shock=r.total_shock,
                shock_list=[
                    ShockRowOut(
                        bizno=row["bizno"], shock=row["shock"], depth=depths.get(row["bizno"])
                    )
                    for row in r.shock_list
                ],
                damped_cycles=[DampedCycleOut(**d) for d in r.damped_cycles],
            )
        )
    return ScenarioResponse(directions=dirs)


# ── 관세(외생) 충격 ────────────────────────────────────────────────────────
# 내부 고정값(tariff·volume 공통) — pin_seeds/method/cycle_damping 은 외부 노출 안 함.
_DEFAULT_PIN_SEEDS = False
_DEFAULT_METHOD = "scc"
_DEFAULT_CYCLE_DAMPING = 0.95


class TariffRequest(BaseModel):
    triple_list: list[TripleIn] = Field(..., description="거래쌍·거래비율 엣지 목록")
    seed_list: list[str] = Field(..., description="외생충격 받는 1차 기업 bizno")
    shock_rate: float = Field(..., description="시드 주입 충격량 (음수 가능)")
    directions: list[int] = Field([1], description="[0|1] — 0=매출, 1=매입 파급")

    model_config = {"json_schema_extra": {"example": _EX_TARIFF_REQ}}


@app.post("/api/shock/tariff", response_model=ScenarioResponse, summary="관세(외생) 충격")
def tariff(req: TariffRequest) -> ScenarioResponse:
    results = run_tariff(
        [t.model_dump(by_alias=True) for t in req.triple_list],
        req.seed_list,
        req.shock_rate,
        req.directions,
        pin_seeds=_DEFAULT_PIN_SEEDS,
        method=_DEFAULT_METHOD,
        cycle_damping=_DEFAULT_CYCLE_DAMPING,
    )
    return _to_out(results)


# ── 거래량 변동 ────────────────────────────────────────────────────────────
class OverrideIn(BaseModel):
    p1: str = Field(..., description="변동 대상 기업 bizno")
    w1: float = Field(..., description="factor = 1+증감율 (0.8=−20%)")


class VolumeRequest(BaseModel):
    triple_list: list[TripleIn]
    seed_list: list[str]
    edge_overrides: list[OverrideIn] = Field(..., description="[{p1,w1}] 노드별 거래량 factor")
    directions: list[int] = Field([1], description="[0|1]")

    model_config = {"json_schema_extra": {"example": _EX_VOLUME_REQ}}


@app.post("/api/shock/volume", response_model=ScenarioResponse, summary="거래량 변동")
def volume(req: VolumeRequest) -> ScenarioResponse:
    # pin_seeds/method/cycle_damping 은 내부 고정 (tariff 와 동일 정책).
    results = run_volume(
        [t.model_dump(by_alias=True) for t in req.triple_list],
        req.seed_list,
        [o.model_dump() for o in req.edge_overrides],
        req.directions,
        pin_seeds=_DEFAULT_PIN_SEEDS,
        method=_DEFAULT_METHOD,
        cycle_damping=_DEFAULT_CYCLE_DAMPING,
    )
    return _to_out(results)


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
