"""1차 충격 Δy / Δcost 계산. 구현명세서 §3, §5, §8.1.

::

    dy = direct_shock.compute(shock, firms, exports=data['exports'])['delta_revenue']

영향 기업 식별(target_type=HS6/KSIC/FIRMLIST)은 호출자가 ``firms`` 를 미리
필터해서 넘긴다(스펙 §5.2 의 Cypher 매칭이 ``data/load_graph`` 단계에서 끝남).
본 모듈은 **순수 수치 변환**만 담당.
"""

from __future__ import annotations

import pandas as pd

from nice_poc.shock import rates
from nice_poc.shock.scenario import Shock


def compute(
    shock: Shock,
    firms: pd.DataFrame,
    *,
    exports: pd.Series | pd.DataFrame | None = None,
) -> dict[str, pd.Series]:
    """1차 충격 절대 금액 (원).

    Parameters
    ----------
    shock : Shock
    firms : DataFrame indexed by firm_id. 호출자가 1차 충격 대상으로 사전 필터해서 넘김.
            반드시 sales_year_fin (또는 폴백) 컬럼 보유.
    exports : optional. TARIFF/GDP 시나리오에서 firm 별 수출액 (firm_id 인덱스의 Series
              또는 (firm_id × HS6 × Country) MultiIndex DataFrame).

    Returns
    -------
    {'delta_revenue': Series, 'delta_cost': Series} — index = firms.index
    """
    rate_rev = rates.annualize(rates.revenue_rate(shock), shock.duration_month)
    rate_cost = rates.annualize(rates.cost_rate(shock), shock.duration_month)

    base_rev = _base_revenue(shock, firms, exports)
    base_cost = _base_cost(shock, firms)

    delta_rev = (base_rev * rate_rev).reindex(firms.index, fill_value=0.0)
    delta_cost = (base_cost * rate_cost).reindex(firms.index, fill_value=0.0)

    return {"delta_revenue": delta_rev, "delta_cost": delta_cost}


def _base_revenue(
    shock: Shock,
    firms: pd.DataFrame,
    exports: pd.Series | pd.DataFrame | None,
) -> pd.Series:
    if shock.input_type in ("TARIFF", "GDP"):
        if exports is None:
            raise ValueError(f"{shock.input_type} requires `exports` argument")
        return _firm_export_amount(exports, firms.index, shock)
    # B2C / GOV / SUPPLY 시나리오 — 매출 폴백
    return _sales(firms)


def _base_cost(shock: Shock, firms: pd.DataFrame) -> pd.Series:
    if shock.is_demand:
        return pd.Series(0.0, index=firms.index)
    # SUPPLY: 비용 = 매입액 (vat_fs_est_purchase) 폴백
    if "vat_fs_est_purchase" in firms.columns:
        return firms["vat_fs_est_purchase"].fillna(0.0).astype("float64")
    return pd.Series(0.0, index=firms.index)


def _sales(firms: pd.DataFrame) -> pd.Series:
    for col in ("sales_year_fin", "sales_year_vat_observed", "vat_fs_est_sales"):
        if col in firms.columns:
            s = firms[col].astype("float64")
            if s.notna().any():
                return s.fillna(0.0)
    return pd.Series(0.0, index=firms.index)


def _firm_export_amount(
    exports: pd.Series | pd.DataFrame,
    firm_ids: pd.Index,
    shock: Shock,
) -> pd.Series:
    if isinstance(exports, pd.Series):
        return exports.reindex(firm_ids, fill_value=0.0).astype("float64")

    df = exports
    if shock.target_value is not None and "hs6" in df.columns:
        target = [shock.target_value] if isinstance(shock.target_value, str) else shock.target_value
        df = df[df["hs6"].isin(target)]
    if shock.target_nation and "iso_alpha2" in df.columns:
        df = df[df["iso_alpha2"].isin(shock.target_nation)]

    summed = df.groupby("firm_id")["amount"].sum() if "firm_id" in df.columns else df.sum(axis=1)
    return summed.reindex(firm_ids, fill_value=0.0).astype("float64")
