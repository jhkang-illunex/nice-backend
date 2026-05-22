"""8 input_type 별 매출/비용 변화율 산식.

구현명세서 §3, §11.2 의 v1.1 부호 보정을 그대로 반영.
모든 함수는 순수 (Shock → float).
연율화(× duration_month/12)는 ``annualize()`` 로 일괄 적용.
"""
from __future__ import annotations

from nice_poc.shock.scenario import Shock


def annualize(rate: float, duration_month: int) -> float:
    return rate * (duration_month / 12.0)


# --- DEMAND ----------------------------------------------------------------

def tariff_revenue_rate(s: Shock) -> float:
    """§11.2.1/2 — 부호 보정 후 (수요변화 + 가격항) 합산.

    delta_p = (1+before) / (1+after) - 1
    demand_change_rate = price_elasticity * pass_through * delta_p
    price_term        = delta_p * pass_through
    revenue_rate      = demand_change_rate + price_term
    """
    assert s.before_tariff is not None
    assert s.after_tariff is not None
    assert s.price_elasticity is not None
    delta_p = (1 + s.before_tariff) / (1 + s.after_tariff) - 1
    demand = s.price_elasticity * s.pass_through * delta_p
    price = s.pass_through * delta_p
    return demand + price


def gdp_revenue_rate(s: Shock) -> float:
    assert s.income_elasticity is not None
    assert s.gdp_growth_rate is not None
    return s.income_elasticity * s.gdp_growth_rate


def b2c_or_gov_revenue_rate(s: Shock) -> float:
    assert s.revenue_value is not None
    return s.revenue_value


# --- SUPPLY ----------------------------------------------------------------

def import_price_cost_rate(s: Shock) -> float:
    """§11.2.3 — Cost_value = Δp_m × (1 + ε_m)."""
    assert s.price_m_change_rate is not None
    elasticity = s.price_m_elasticity or 0.0
    return s.price_m_change_rate * (1 + elasticity)


def import_price_revenue_rate(s: Shock) -> float:
    """전가율 × Δp_m (단순화)."""
    assert s.price_m_change_rate is not None
    return s.pass_through * s.price_m_change_rate


def shutdown_revenue_rate(s: Shock) -> float:
    """§11.2.4 — 매출 변화는 음수. Δrev = -(1-σ) × shutdown."""
    assert s.import_change is not None
    return -(1 - s.substitute_elasticity) * s.import_change


def shutdown_cost_rate(s: Shock) -> float:
    """비용 변화 (음수)."""
    assert s.import_change is not None
    return -s.import_change


def domestic_price_cost_rate(s: Shock) -> float:
    assert s.price_domestic_value is not None
    return s.price_domestic_value


def domestic_price_revenue_rate(s: Shock) -> float:
    assert s.price_domestic_value is not None
    return s.pass_through * s.price_domestic_value


# --- dispatch --------------------------------------------------------------

def revenue_rate(s: Shock) -> float:
    match s.input_type:
        case "TARIFF":
            return tariff_revenue_rate(s)
        case "GDP":
            return gdp_revenue_rate(s)
        case "B2C_REVENUE" | "GOV_REVENUE":
            return b2c_or_gov_revenue_rate(s)
        case "IMPORT_PRICE":
            return import_price_revenue_rate(s)
        case "IMPORT_SHUTDOWN" | "DOMESTIC_SHUTDOWN":
            return shutdown_revenue_rate(s)
        case "DOMESTIC_PRICE":
            return domestic_price_revenue_rate(s)
        case _:
            raise ValueError(f"unknown input_type: {s.input_type}")


def cost_rate(s: Shock) -> float:
    match s.input_type:
        case "IMPORT_PRICE":
            return import_price_cost_rate(s)
        case "IMPORT_SHUTDOWN" | "DOMESTIC_SHUTDOWN":
            return shutdown_cost_rate(s)
        case "DOMESTIC_PRICE":
            return domestic_price_cost_rate(s)
        case _:
            return 0.0  # DEMAND 시나리오는 비용 변화 없음
