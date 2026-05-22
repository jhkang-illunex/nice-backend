"""impact_table 9컬럼 빌더. 구현명세서 §11.B2 / Simulation_Results R45~R56.

DEMAND-only 시나리오는 cost_* = 0, profit_* = revenue_*.
SUPPLY 시나리오는 ``supply`` 인자에 leontief.propagate_supply_split 결과를 그대로 넘긴다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

IMPACT_COLUMNS = (
    "revenue_initial",
    "revenue_propagation",
    "revenue_sum",
    "cost_initial",
    "cost_propagation",
    "cost_sum",
    "profit_initial",
    "profit_propagation",
    "profit_sum",
)


@dataclass(frozen=True, slots=True)
class ImpactRecord:
    firm_id: str
    revenue_initial: float
    revenue_propagation: float
    revenue_sum: float
    cost_initial: float
    cost_propagation: float
    cost_sum: float
    profit_initial: float
    profit_propagation: float
    profit_sum: float

    def as_dict(self) -> dict[str, float | str]:
        return asdict(self)


def build_impact_table(
    firm_ids: pd.Index,
    demand: dict[str, np.ndarray] | None = None,
    supply: dict[str, np.ndarray] | None = None,
) -> pd.DataFrame:
    """``firm_ids`` 인덱스의 DataFrame[9 컬럼] 반환.

    ``demand`` : propagate_demand_split 결과 (initial / propagation / total)
    ``supply`` : propagate_supply_split 결과 (cost_*, revenue_*)
    """
    n = len(firm_ids)
    z = np.zeros(n)

    rev_init = z.copy()
    rev_prop = z.copy()
    rev_sum = z.copy()

    cost_init = z.copy()
    cost_prop = z.copy()
    cost_sum = z.copy()

    if demand is not None:
        rev_init = demand["initial"].astype("float64", copy=True)
        rev_prop = demand["propagation"].astype("float64", copy=True)
        rev_sum = demand["total"].astype("float64", copy=True)

    if supply is not None:
        # SUPPLY 의 revenue 는 demand 와 덧합(MIXED 시나리오 대비)
        rev_init = rev_init + supply["revenue_initial"]
        rev_prop = rev_prop + supply["revenue_propagation"]
        rev_sum = rev_sum + supply["revenue_total"]

        cost_init = supply["cost_initial"].astype("float64", copy=True)
        cost_prop = supply["cost_propagation"].astype("float64", copy=True)
        cost_sum = supply["cost_total"].astype("float64", copy=True)

    profit_init = rev_init - cost_init
    profit_prop = rev_prop - cost_prop
    profit_sum_ = rev_sum - cost_sum

    return pd.DataFrame(
        {
            "revenue_initial": rev_init,
            "revenue_propagation": rev_prop,
            "revenue_sum": rev_sum,
            "cost_initial": cost_init,
            "cost_propagation": cost_prop,
            "cost_sum": cost_sum,
            "profit_initial": profit_init,
            "profit_propagation": profit_prop,
            "profit_sum": profit_sum_,
        },
        index=firm_ids,
    )


__all__ = ["ImpactRecord", "IMPACT_COLUMNS", "build_impact_table"]
