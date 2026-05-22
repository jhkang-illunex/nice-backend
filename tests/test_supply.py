"""구현명세서 §11.4 — IMPORT_PRICE / SHUTDOWN 산식 + 매출 음수 부호."""
from __future__ import annotations

import numpy as np

from nice_poc.shock import rates
from nice_poc.shock.scenario import Shock


def test_import_price_cost_includes_elasticity_v11() -> None:
    s = Shock(
        shock_id="SH", scenario_id="SC", shock_type="수입",
        target_type="HS6", input_type="IMPORT_PRICE",
        price_m_change_rate=0.10, price_m_elasticity=-0.5, pass_through=0.8,
    )
    cost_rate = rates.import_price_cost_rate(s)
    np.testing.assert_allclose(cost_rate, 0.10 * (1 + (-0.5)))


def test_import_shutdown_revenue_negative_v11() -> None:
    s = Shock(
        shock_id="SH", scenario_id="SC", shock_type="수입",
        target_type="HS6", input_type="IMPORT_SHUTDOWN",
        import_change=0.30, substitute_elasticity=0.0,
    )
    rev_rate = rates.shutdown_revenue_rate(s)
    assert rev_rate < 0
    np.testing.assert_allclose(rev_rate, -0.30)


def test_shutdown_with_substitute_buffer() -> None:
    s = Shock(
        shock_id="SH", scenario_id="SC", shock_type="수입",
        target_type="HS6", input_type="IMPORT_SHUTDOWN",
        import_change=0.30, substitute_elasticity=0.4,
    )
    rev_rate = rates.shutdown_revenue_rate(s)
    np.testing.assert_allclose(rev_rate, -(1 - 0.4) * 0.30)


def test_demand_shock_has_zero_cost_rate() -> None:
    s = Shock(
        shock_id="SH", scenario_id="SC", shock_type="B2C",
        target_type="KSIC", input_type="B2C_REVENUE",
        revenue_value=-0.1,
    )
    assert rates.cost_rate(s) == 0.0
