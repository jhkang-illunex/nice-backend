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

from nice_shock.scenario import run_tariff, run_volume

app = FastAPI(
    title="NICE shock-propagate",
    version="1.0.0",
    description="triple_list 입력 순수 쇼크 전파 — 관세충격 / 거래량변동.",
)


# ── 공통 스키마 ────────────────────────────────────────────────────────────
class TripleIn(BaseModel):
    from_: str = Field(..., alias="from", description="셀러(거래 출발) bizno")
    to: str = Field(..., description="바이어(거래 도착) bizno")
    rate: float = Field(..., description="거래비율(trade_rate, source 정규화)")

    model_config = {"populate_by_name": True}


class ShockRowOut(BaseModel):
    bizno: str
    shock: float


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


class ScenarioResponse(BaseModel):
    directions: list[DirectionOut]


_METHODS = ("scc", "iterative")


def _to_out(results) -> ScenarioResponse:
    dirs = []
    for dr in results:
        r = dr["result"]
        dirs.append(
            DirectionOut(
                direction=dr["direction"],
                converged=r.converged,
                iterations=r.iterations,
                total_shock=r.total_shock,
                shock_list=[ShockRowOut(**row) for row in r.shock_list],
                damped_cycles=[DampedCycleOut(**d) for d in r.damped_cycles],
            )
        )
    return ScenarioResponse(directions=dirs)


# ── 관세(외생) 충격 ────────────────────────────────────────────────────────
class TariffRequest(BaseModel):
    triple_list: list[TripleIn] = Field(..., description="거래쌍·거래비율 엣지 목록")
    seed_list: list[str] = Field(..., description="외생충격 받는 1차 기업 bizno")
    shock_rate: float = Field(..., description="시드 주입 충격량 (음수 가능)")
    directions: list[int] = Field([1], description="[0|1] — 0=매출, 1=매입 파급")
    pin_seeds: bool = Field(True, description="시드 incoming 차단(주입값 고정·자기증폭 방지)")
    method: str = Field("scc", description="scc(닫힌해) | iterative(반복)")
    cycle_damping: float = Field(0.95, gt=0.0, lt=1.0, description="ρ≥1 순환 조건부 damping")


@app.post("/api/shock/tariff", response_model=ScenarioResponse, summary="관세(외생) 충격")
def tariff(req: TariffRequest) -> ScenarioResponse:
    results = run_tariff(
        [t.model_dump(by_alias=True) for t in req.triple_list],
        req.seed_list,
        req.shock_rate,
        req.directions,
        pin_seeds=req.pin_seeds,
        method=req.method if req.method in _METHODS else "scc",
        cycle_damping=req.cycle_damping,
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
    pin_seeds: bool = True
    method: str = "scc"
    cycle_damping: float = Field(0.95, gt=0.0, lt=1.0)


@app.post("/api/shock/volume", response_model=ScenarioResponse, summary="거래량 변동")
def volume(req: VolumeRequest) -> ScenarioResponse:
    results = run_volume(
        [t.model_dump(by_alias=True) for t in req.triple_list],
        req.seed_list,
        [o.model_dump() for o in req.edge_overrides],
        req.directions,
        pin_seeds=req.pin_seeds,
        method=req.method if req.method in _METHODS else "scc",
        cycle_damping=req.cycle_damping,
    )
    return _to_out(results)


@app.get("/health", summary="헬스체크")
def health() -> dict:
    return {"status": "ok", "service": "shock-propagate"}
