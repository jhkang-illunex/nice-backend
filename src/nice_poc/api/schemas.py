"""화면 ①~⑦ + 검색 API 응답 스키마. 구현명세서 §2 PG impacts/firms 와 1:1 매핑.

데이터 적재 전이라 라우터는 501 을 반환하지만, OpenAPI contract 는
프론트엔드와 합의된 형태 그대로 노출하여 클라이언트 개발이 병렬 진행 가능하도록 한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# ─────────────────────────────────────────────────────────────────────────────
# 공통
# ─────────────────────────────────────────────────────────────────────────────

T = TypeVar("T")


class Paginated(BaseModel, Generic[T]):
    items: list[T]
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=1000)
    total: int = Field(ge=0)


# ─────────────────────────────────────────────────────────────────────────────
# 화면 ① KPI 6 카드
# ─────────────────────────────────────────────────────────────────────────────


class KpiCard(BaseModel):
    """RUN_ID 별 전국 집계 6 지표."""

    revenue_total: float
    cost_total: float
    profit_total: float
    capped_ratio: float = Field(ge=0.0, le=1.0, description="max_delta cap 발동 비율")
    firm_count: int = Field(ge=0)
    top_severity: float = Field(description="가장 큰 1차 충격 강도(절댓값)")


# ─────────────────────────────────────────────────────────────────────────────
# 화면 ② 시나리오 입력 / 실행
# ─────────────────────────────────────────────────────────────────────────────


ScenarioTypeLiteral = Literal["DEMAND", "SUPPLY", "MIXED"]
TargetTypeLiteral = Literal["HS6", "KSIC", "FIRMLIST"]
InputTypeLiteral = Literal[
    # DEMAND
    "TARIFF",
    "GDP",
    "B2C_REVENUE",
    "GOV_REVENUE",
    # SUPPLY
    "IMPORT_PRICE",
    "IMPORT_SHUTDOWN",
    "DOMESTIC_PRICE",
    "DOMESTIC_SHUTDOWN",
]


class ShockCreate(BaseModel):
    """Scenario 1 개에 N 개. 명세서 §3 Shock dataclass 와 동일 필드 셋."""

    model_config = ConfigDict(extra="forbid")

    shock_type: str
    target_type: TargetTypeLiteral
    input_type: InputTypeLiteral
    target_value: str | list[str] | None = None
    target_nation: list[str] | None = None

    # DEMAND
    before_tariff: float | None = None
    after_tariff: float | None = None
    price_value: float | None = None
    pass_through: float = 1.0
    price_elasticity: float | None = None
    gdp_growth_rate: float | None = None
    income_elasticity: float | None = None
    revenue_value: float | None = None

    # SUPPLY
    price_m_change_rate: float | None = None
    price_m_elasticity: float | None = None
    import_change: float | None = None
    substitute_elasticity: float = 0.0
    capacity_value: float | None = None
    cost_value: float | None = None
    price_domestic_value: float | None = None
    profit_value: float | None = None

    duration_month: int = 12


class ScenarioCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_name: str
    scenario_type: ScenarioTypeLiteral
    version: str = "v1"
    scenario_group_id: str | None = None
    scenario_group_name: str | None = None
    shocks: list[ShockCreate] = Field(default_factory=list)


class ScenarioOut(BaseModel):
    scenario_id: str
    scenario_name: str
    scenario_type: ScenarioTypeLiteral
    scenario_seq: int | None = None
    version: str
    scenario_group_id: str | None = None
    scenario_group_name: str | None = None
    created_at: datetime
    shock_count: int = Field(ge=0)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    target_year: int = Field(ge=1900, le=2100)


RunStatusLiteral = Literal["QUEUED", "RUNNING", "DONE", "FAILED"]


class RunOut(BaseModel):
    run_id: str
    scenario_id: str
    target_year: int
    status: RunStatusLiteral
    executed_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# 화면 ③ 네트워크 subgraph
# ─────────────────────────────────────────────────────────────────────────────


class NetworkNode(BaseModel):
    firm_id: str
    firm_name: str
    x: float
    y: float
    size: float = Field(ge=0.0)


class NetworkEdge(BaseModel):
    source: str
    target: str
    weight: float


class NetworkSubgraph(BaseModel):
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]


# ─────────────────────────────────────────────────────────────────────────────
# 화면 ④/⑤ 기업 상세 / 영향 기업 리스트
# ─────────────────────────────────────────────────────────────────────────────


class FirmImpact(BaseModel):
    """impacts 9 컬럼 + 식별/요약. 명세서 §11.B2 R45~R56 와 정합."""

    firm_id: str
    firm_name: str
    sector_code: str | None = None

    revenue_initial: float
    revenue_propagation: float
    revenue_sum: float
    cost_initial: float
    cost_propagation: float
    cost_sum: float
    profit_initial: float
    profit_propagation: float
    profit_sum: float

    impact_score: float = Field(description="정렬용 합성 지표(절댓값 기반)")
    capped: bool = Field(description="max_delta cap 발동 여부")


class FirmDetail(FirmImpact):
    """화면 ④. FirmImpact + firms 메타 일부."""

    cri_score: float | None = None
    sales_year_fin: float | None = None


class PathStep(BaseModel):
    firm_id: str
    firm_name: str
    weight: float
    hop: int = Field(ge=0)


class PathResponse(BaseModel):
    steps: list[PathStep]
    total_hops: int = Field(ge=0)


# ─────────────────────────────────────────────────────────────────────────────
# 화면 ⑥ 산업/본사 집계
# ─────────────────────────────────────────────────────────────────────────────


class SectorAggregate(BaseModel):
    sector_code: str
    sector_name: str | None = None
    revenue_total: float
    cost_total: float
    profit_total: float
    firm_count: int = Field(ge=0)


# ─────────────────────────────────────────────────────────────────────────────
# 화면 ⑦ 시계열
# ─────────────────────────────────────────────────────────────────────────────


class TimeseriesPoint(BaseModel):
    scenario_seq: int
    scenario_name: str
    revenue_sum: float
    cost_sum: float
    profit_sum: float


# ─────────────────────────────────────────────────────────────────────────────
# 검색
# ─────────────────────────────────────────────────────────────────────────────


class AutocompleteHit(BaseModel):
    firm_id: str
    firm_name: str
    similarity: float = Field(ge=0.0, le=1.0)


class SemanticHit(BaseModel):
    firm_id: str
    firm_name: str
    score: float


__all__ = [
    "Paginated",
    "KpiCard",
    "ShockCreate",
    "ScenarioCreate",
    "ScenarioOut",
    "RunCreate",
    "RunOut",
    "NetworkNode",
    "NetworkEdge",
    "NetworkSubgraph",
    "FirmImpact",
    "FirmDetail",
    "PathStep",
    "PathResponse",
    "SectorAggregate",
    "TimeseriesPoint",
    "AutocompleteHit",
    "SemanticHit",
]
