"""구현명세서 §11.4 test_aggregate.py — Summary 12키 + Scenario_Summary."""
from __future__ import annotations

import numpy as np
import pandas as pd

from nice_poc.result import aggregate, impact_record


def _make_table(values: dict[str, list[float]]) -> pd.DataFrame:
    idx = pd.Index([f"F{i:03d}" for i in range(len(next(iter(values.values()))))],
                   name="firm_id")
    return pd.DataFrame(values, index=idx)


def test_build_impact_table_demand_only() -> None:
    fid = pd.Index(["A", "B", "C"], name="firm_id")
    demand = {
        "initial":     np.array([100.0, 0.0, 0.0]),
        "propagation": np.array([0.0, 30.0, 5.0]),
        "total":       np.array([100.0, 30.0, 5.0]),
    }
    tab = impact_record.build_impact_table(fid, demand=demand)

    np.testing.assert_allclose(tab["revenue_sum"], [100, 30, 5])
    np.testing.assert_allclose(tab["cost_sum"], [0, 0, 0])
    # DEMAND-only 면 profit_* = revenue_*
    np.testing.assert_allclose(tab["profit_sum"], tab["revenue_sum"])


def test_build_impact_table_supply_adds_revenue_and_cost() -> None:
    fid = pd.Index(["A", "B"], name="firm_id")
    supply = {
        "cost_initial":        np.array([10.0, 0.0]),
        "cost_propagation":    np.array([0.0, 2.0]),
        "cost_total":          np.array([10.0, 2.0]),
        "revenue_initial":     np.array([5.0, 0.0]),
        "revenue_propagation": np.array([0.0, 1.0]),
        "revenue_total":       np.array([5.0, 1.0]),
    }
    tab = impact_record.build_impact_table(fid, supply=supply)
    np.testing.assert_allclose(tab["profit_sum"], [5 - 10, 1 - 2])


def test_summary_card_full_has_12_keys_with_run_id() -> None:
    tab = _make_table({
        "revenue_initial":     [100, 0,   0],
        "revenue_propagation": [0,   30,  0],
        "revenue_sum":         [100, 30,  0],
        "cost_initial":        [0,   0,   0],
        "cost_propagation":    [0,   0,   0],
        "cost_sum":            [0,   0,   0],
        "profit_initial":      [100, 0,   0],
        "profit_propagation":  [0,   30,  0],
        "profit_sum":          [100, 30,  0],
    })

    s = aggregate.summary_card_full(tab, run_id="R1")

    assert set(aggregate.SUMMARY_KEYS).issubset(s.keys())
    assert s["run_id"] == "R1"
    assert s["Revenue_total_Sum"] == 130.0
    assert s["Firm_total_number"] == 2  # 마지막 행은 0 매출
    assert s["Firm_total_list"] == ["F000", "F001"]


def test_by_scenario_seq_returns_indexed_df() -> None:
    t1 = _make_table({c: [1, 1] for c in impact_record.IMPACT_COLUMNS})
    t2 = _make_table({c: [10, 10] for c in impact_record.IMPACT_COLUMNS})

    df = aggregate.by_scenario_seq({1: t1, 2: t2})
    assert df.index.name == "scenario_seq"
    assert df.loc[1, "Revenue_total_Sum"] == 2.0
    assert df.loc[2, "Revenue_total_Sum"] == 20.0


def test_affected_firms_sorted_by_abs_revenue() -> None:
    tab = _make_table({
        "revenue_initial":     [0, 0, 0, 0],
        "revenue_propagation": [0, 0, 0, 0],
        "revenue_sum":         [50, -300, 100, 10],
        "cost_initial":        [0, 0, 0, 0],
        "cost_propagation":    [0, 0, 0, 0],
        "cost_sum":            [0, 0, 0, 0],
        "profit_initial":      [0, 0, 0, 0],
        "profit_propagation":  [0, 0, 0, 0],
        "profit_sum":          [50, -300, 100, 10],
    })
    out = aggregate.affected_firms(tab, top_n=3)
    assert list(out.index) == ["F001", "F002", "F000"]
