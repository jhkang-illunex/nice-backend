"""구현명세서 §9.1 / §11.4 — TARIFF 산식의 v1.1 부호 보정 검증."""
from __future__ import annotations

import numpy as np
import pandas as pd

from nice_poc.shock import direct_shock, rates
from nice_poc.shock.scenario import Shock


def _tariff_shock(**over: float | int) -> Shock:
    defaults = dict(
        shock_id="SH",
        scenario_id="SC",
        shock_type="수출",
        target_type="HS6",
        input_type="TARIFF",
        before_tariff=0.0,
        after_tariff=0.25,
        pass_through=1.0,
        price_elasticity=-1.2,
        duration_month=12,
    )
    defaults.update(over)
    return Shock(**defaults)


def test_tariff_delta_p_sign_v11() -> None:
    """관세 인상 → delta_p 는 음수, 수요변화는 양수, 가격항은 음수."""
    s = _tariff_shock()
    delta_p = (1 + 0.0) / (1 + 0.25) - 1
    assert delta_p < 0
    demand = s.price_elasticity * s.pass_through * delta_p
    price = s.pass_through * delta_p
    assert demand > 0  # 음 × 음 = 양
    assert price < 0
    rate = rates.tariff_revenue_rate(s)
    np.testing.assert_allclose(rate, demand + price)


def test_tariff_duration_annualization() -> None:
    s_full = _tariff_shock(duration_month=12)
    s_half = _tariff_shock(duration_month=6)

    firms = pd.DataFrame(index=pd.Index(["A"], name="firm_id"))
    exports = pd.Series({"A": 1_000_000.0}, name="amount")

    full = direct_shock.compute(s_full, firms, exports=exports)["delta_revenue"]
    half = direct_shock.compute(s_half, firms, exports=exports)["delta_revenue"]

    np.testing.assert_allclose(half["A"], full["A"] / 2)


def test_tariff_pass_through_proportional() -> None:
    s_one = _tariff_shock(pass_through=1.0)
    s_half = _tariff_shock(pass_through=0.5)

    one = rates.tariff_revenue_rate(s_one)
    half = rates.tariff_revenue_rate(s_half)

    np.testing.assert_allclose(half, one * 0.5)
