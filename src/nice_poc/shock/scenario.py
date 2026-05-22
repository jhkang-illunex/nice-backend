"""Scenario / Shock / ScenarioGroup dataclass. 구현명세서 §3, §11.B6."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

DemandInput = Literal["TARIFF", "GDP", "B2C_REVENUE", "GOV_REVENUE"]
SupplyInput = Literal[
    "IMPORT_PRICE", "IMPORT_SHUTDOWN", "DOMESTIC_PRICE", "DOMESTIC_SHUTDOWN"
]
InputType = DemandInput | SupplyInput
TargetType = Literal["HS6", "KSIC", "FIRMLIST"]
ScenarioType = Literal["DEMAND", "SUPPLY", "MIXED"]


@dataclass(frozen=True, slots=True)
class ScenarioGroup:
    scenario_group_id: str
    scenario_group_name: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    scenario_name: str
    scenario_type: ScenarioType
    scenario_seq: int | None = None
    version: str = "v1"
    scenario_group_id: str | None = None
    scenario_group_name: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class Shock:
    """모든 input_type 의 파라미터를 한 라벨로 보유. 미사용은 None."""
    shock_id: str
    scenario_id: str
    shock_type: str          # 수출 / B2C / Govt / 수입 / 국내
    target_type: TargetType
    input_type: InputType
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

    @property
    def is_demand(self) -> bool:
        return self.input_type in ("TARIFF", "GDP", "B2C_REVENUE", "GOV_REVENUE")

    @property
    def is_supply(self) -> bool:
        return not self.is_demand
