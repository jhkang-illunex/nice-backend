"""ShockResult → Streamlit 표/차트.

핵심 지표 우선순위
  1. TIS 상위 N — Exposure × Risk (가장 정책 의미 있는 정렬 키)
  2. revenue_sum (Δ매출 합)
  3. profit_sum  (Δ이익 합)
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from nice_demo.pipeline.shock_runner import ShockResult


def render_diagnostics(result: ShockResult) -> None:
    """rho / normalized / capped_ratio / shock 설정 한눈에."""
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("ρ(H) — spectral radius", f"{result.rho:.4f}")
    c2.metric("Row-normalized?", "Yes" if result.normalized else "No")
    c3.metric("Capped firm ratio", f"{result.capped_ratio:.1%}")
    c4.metric("Scenario type", result.shock.input_type)


def render_top_tis(result: ShockResult, *, top_n: int = 20) -> pd.DataFrame:
    st.subheader(f"TIS (Trade Impact Score) 상위 {top_n}")
    tis_df = result.tis_table.copy()
    tis_df = tis_df.sort_values("tis", ascending=False).head(top_n)
    st.dataframe(
        tis_df.style.format(
            {"exposure": "{:.2%}", "risk": "{:.2f}", "tis": "{:.4f}"}
        )
    )
    st.bar_chart(tis_df["tis"])
    return tis_df


def render_impact_table(result: ShockResult, *, top_n: int = 30) -> None:
    st.subheader(f"Impact table — revenue_sum 절대값 상위 {top_n}")
    df = result.impact_table.copy()
    df = df.reindex(
        df["revenue_sum"].abs().sort_values(ascending=False).head(top_n).index
    )
    st.dataframe(
        df.style.format(
            {
                "revenue_initial": "{:,.0f}",
                "revenue_propagation": "{:,.0f}",
                "revenue_sum": "{:,.0f}",
                "cost_initial": "{:,.0f}",
                "cost_propagation": "{:,.0f}",
                "cost_sum": "{:,.0f}",
                "profit_initial": "{:,.0f}",
                "profit_propagation": "{:,.0f}",
                "profit_sum": "{:,.0f}",
            }
        )
    )
    st.download_button(
        label="Impact table 전체 CSV 다운로드",
        data=result.impact_table.to_csv().encode("utf-8-sig"),
        file_name=f"impact_table_{result.shock.shock_id}.csv",
        mime="text/csv",
    )
