"""Summary / Scenario_Summary / affected firms. 구현명세서 §11.B3, §11.B4."""

from __future__ import annotations

from typing import Any

import pandas as pd

SUMMARY_KEYS = (
    "Revenue_total_Sum",
    "Revenue_total_initial",
    "Revenue_total_propagation",
    "Cost_total_Sum",
    "Cost_total_initial",
    "Cost_total_propagation",
    "Profit_Sum",
    "Profit_total_initial",
    "Profit_total_propagation",
    "Firm_total_number",
    "Firm_total_list",
)


def summary_card_full(
    impact_table: pd.DataFrame,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    affected = impact_table["revenue_sum"] != 0

    out: dict[str, Any] = {
        "Revenue_total_Sum": float(impact_table["revenue_sum"].sum()),
        "Revenue_total_initial": float(impact_table["revenue_initial"].sum()),
        "Revenue_total_propagation": float(impact_table["revenue_propagation"].sum()),
        "Cost_total_Sum": float(impact_table["cost_sum"].sum()),
        "Cost_total_initial": float(impact_table["cost_initial"].sum()),
        "Cost_total_propagation": float(impact_table["cost_propagation"].sum()),
        "Profit_Sum": float(impact_table["profit_sum"].sum()),
        "Profit_total_initial": float(impact_table["profit_initial"].sum()),
        "Profit_total_propagation": float(impact_table["profit_propagation"].sum()),
        "Firm_total_number": int(affected.sum()),
        "Firm_total_list": impact_table.index[affected].astype(str).tolist(),
    }
    if run_id is not None:
        out["run_id"] = run_id
    return out


def by_scenario_seq(per_seq: dict[int, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for seq in sorted(per_seq):
        s = summary_card_full(per_seq[seq])
        s.pop("Firm_total_list", None)
        rows.append({"scenario_seq": seq, **s})
    return pd.DataFrame(rows).set_index("scenario_seq")


def affected_firms(
    impact_table: pd.DataFrame,
    firms: pd.DataFrame | None = None,
    *,
    impact_score: pd.Series | None = None,
    top_n: int = 50,
    sort_by: str = "revenue_sum",
) -> pd.DataFrame:
    """영향 큰 기업 상위 N 정렬. 화면 ⑤ 영향 기업 리스트."""
    ranked = impact_table.reindex(
        impact_table[sort_by].abs().sort_values(ascending=False).index
    ).head(top_n)

    if firms is not None:
        meta_cols = [c for c in ("firm_name", "sector_code", "cri_score") if c in firms.columns]
        if meta_cols:
            ranked = ranked.join(firms[meta_cols], how="left")

    if impact_score is not None:
        ranked = ranked.assign(impact_score=impact_score.reindex(ranked.index).fillna(0.0))

    return ranked


__all__ = ["SUMMARY_KEYS", "summary_card_full", "by_scenario_seq", "affected_firms"]
