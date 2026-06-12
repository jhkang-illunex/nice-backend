"""1차 업체 리스트 → Leontief 쇼크 산출 — nice_poc 파이프라인 재사용.

호출 흐름::

    direct_shock.compute(...)         # Δrevenue / Δcost (필터된 firms 만)
    matrix_H.build(...)               # 부분 그래프 H 행렬
    spectral_radius.check_and_normalize(H)
    leontief.propagate_demand_split(H_safe, dy)
    max_delta_cap.cap_revenue(total, sales)
    impact_record.build_impact_table + tis.compute
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from nice_poc.indicator import tis
from nice_poc.matrix import matrix_H
from nice_poc.propagation import leontief
from nice_poc.result import impact_record
from nice_poc.safety import max_delta_cap, spectral_radius
from nice_poc.shock import direct_shock
from nice_poc.shock.scenario import Shock

log = logging.getLogger(__name__)


@dataclass
class ShockResult:
    impact_table: pd.DataFrame  # 9컬럼 (revenue/cost/profit × initial/propagation/sum)
    tis_table: pd.DataFrame  # exposure / risk / tis
    rho: float  # spectral radius (안전성 진단)
    normalized: bool  # row-normalize 됐는지
    capped_ratio: float  # cap 적용된 firm 비율
    shock: Shock


def run(
    *,
    firms: pd.DataFrame,
    edges: pd.DataFrame,
    exports: pd.Series,
    primary_bizno: list[str],
    trade_year: int,
    shock_params: dict | None = None,
) -> ShockResult:
    """부분 그래프 + 1차 업체 → 쇼크 결과.

    ``shock_params`` 은 Shock dataclass 의 일부 필드를 dict 로 전달 (UI 입력값).
    기본은 SUPPLY/IMPORT_PRICE 시나리오 (해외에서 오는 가격 충격).
    """
    sp = shock_params or {}
    shock = Shock(
        shock_id=sp.get("shock_id", "DEMO-SHK-1"),
        scenario_id=sp.get("scenario_id", "DEMO-SCN-1"),
        shock_type=sp.get("shock_type", "수입"),
        target_type="FIRMLIST",
        input_type=sp.get("input_type", "IMPORT_PRICE"),
        target_value=primary_bizno,
        target_nation=sp.get("target_nation"),
        price_m_change_rate=sp.get("price_m_change_rate", 0.20),
        price_m_elasticity=sp.get("price_m_elasticity", -1.0),
        import_change=sp.get("import_change"),
        substitute_elasticity=sp.get("substitute_elasticity", 0.0),
        duration_month=int(sp.get("duration_month", 12)),
    )

    # 1) 1차 업체만으로 필터된 firms (direct_shock 가 요구 — §5 호출자 책임)
    firms_primary = firms.reindex(pd.Index(primary_bizno)).dropna(
        how="all", subset=["sales_year_fin"]
    )
    if firms_primary.empty:
        # 1차 업체 메타가 전부 누락이라도 firms 전체에서 인덱스만 살려 0 충격 산출
        firms_primary = firms.reindex(pd.Index(primary_bizno)).fillna(0.0)

    shocks = direct_shock.compute(shock, firms_primary, exports=exports)
    delta_rev_primary = shocks["delta_revenue"]
    delta_cost_primary = shocks["delta_cost"]

    # 2) 부분 그래프 H — 시드+확장된 전체 노드 사용 (1차 업체 ⊆ 전체)
    h_obj = matrix_H.build(edges, firms, year=int(trade_year))
    H_safe, rho, normalized = spectral_radius.check_and_normalize(h_obj.H)

    # 3) Δy 를 전체 firm_ids 차원으로 align (1차 외 firm 은 0)
    dy = (
        delta_rev_primary.reindex(h_obj.firm_ids, fill_value=0.0)
        .to_numpy(dtype="float64")
    )
    dy_supply_cost = (
        delta_cost_primary.reindex(h_obj.firm_ids, fill_value=0.0)
        .to_numpy(dtype="float64")
    )

    # 4) Leontief 전파 — DEMAND 분기와 SUPPLY 매출/비용 분기
    demand_split = leontief.propagate_demand_split(H_safe, dy)

    if shock.is_supply:
        supply_split = leontief.propagate_supply_split(
            H_safe,
            delta_cost=dy_supply_cost,
            delta_revenue=dy,  # SUPPLY 매출 영향도 같이 (B 미구현 → H.T 폴백)
        )
    else:
        supply_split = None

    # 5) revenue total cap — 매출 100% 초과 cap (수치 안정성)
    total_series = pd.Series(
        demand_split["total"], index=h_obj.firm_ids, name="revenue_total"
    )
    capped, flag = max_delta_cap.cap_revenue(total_series, firms["sales_year_fin"])
    # cap 결과를 demand_split 에 반영
    demand_split = dict(demand_split)
    demand_split["total"] = capped.to_numpy()
    demand_split["propagation"] = (
        capped.to_numpy() - demand_split["initial"]
    )

    # 6) impact_table + TIS
    impact_df = impact_record.build_impact_table(
        h_obj.firm_ids,
        demand=demand_split,
        supply=supply_split,
    )
    tis_df = tis.compute(
        pd.Series(impact_df["revenue_sum"].to_numpy(), index=h_obj.firm_ids),
        firms,
    )

    return ShockResult(
        impact_table=impact_df,
        tis_table=tis_df,
        rho=float(rho),
        normalized=bool(normalized),
        capped_ratio=float(max_delta_cap.capped_ratio(flag)),
        shock=shock,
    )


def safe_run(
    *,
    firms: pd.DataFrame,
    edges: pd.DataFrame,
    exports: pd.Series,
    primary_bizno: list[str],
    trade_year: int,
    shock_params: dict | None = None,
) -> tuple[ShockResult | None, str | None]:
    """예외를 dict 로 swallow — Streamlit UI 가 에러 메시지를 직접 표시할 수 있게."""
    try:
        return run(
            firms=firms,
            edges=edges,
            exports=exports,
            primary_bizno=primary_bizno,
            trade_year=trade_year,
            shock_params=shock_params,
        ), None
    except Exception as exc:
        log.exception("shock pipeline failed")
        return None, f"{exc.__class__.__name__}: {exc}"
