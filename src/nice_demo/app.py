"""NICE 데모 — HS 검색 → 시드 → 3차 확장 → LLM 1차 선정 → 쇼크 산출.

기동::

    streamlit run src/nice_demo/app.py

7 단계를 st.session_state 로 이어가는 위저드 형태. 각 단계는 결과를 세션에
저장하고 다음 단계 버튼이 활성화될 때만 다음으로 진행 가능.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd
import streamlit as st

from nice_demo.clients import get_llm_json_client, get_rag_client
from nice_demo.pipeline import shock_runner
from nice_demo.pipeline import subgraph as sub
from nice_demo.queries import s_ra603
from nice_demo.render import graph_view, shock_view

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


# ── 사이드바 입력 ────────────────────────────────────────────────────────────


def sidebar_inputs() -> dict[str, Any]:
    st.sidebar.title("NICE 데모 — 외생 충격 시나리오")
    st.sidebar.caption(
        "HS → 시드 → 3차 확장 → LLM 1차 선정 → Leontief 쇼크 산출"
    )

    query = st.sidebar.text_input(
        "사용자 질의 (한국어/영문)", value="철광석", help="HS 검색에 사용될 키워드"
    )

    years = s_ra603.available_years() or ["2024", "2023", "2022"]
    # edge.trade_year 가 현재 2024 단일 — 2024 가 있으면 default 로.
    default_idx = years.index("2024") if "2024" in years else 0
    year = st.sidebar.selectbox(
        "거래 연도",
        years,
        index=default_idx,
        help="현재 데모 데이터: public.edge.trade_year = 2024 only.",
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("그래프 확장")
    depth = st.sidebar.slider("BFS depth", 1, 3, value=3)
    top_k = st.sidebar.slider("hop 당 top-K sly_amt edge", 10, 200, value=50, step=10)

    st.sidebar.markdown("---")
    st.sidebar.subheader("쇼크 시나리오 (1차 업체 대상)")
    input_type = st.sidebar.selectbox(
        "input_type",
        ["IMPORT_PRICE", "IMPORT_SHUTDOWN", "DOMESTIC_PRICE", "TARIFF"],
        index=0,
        help="해외에서 오는 충격: IMPORT_PRICE / IMPORT_SHUTDOWN",
    )
    price_m_change_rate = st.sidebar.number_input(
        "수입 가격 변화율 (예 0.20 = +20%)", value=0.20, step=0.05, format="%.2f"
    )
    duration_month = st.sidebar.slider("지속 기간 (월)", 1, 24, value=12)

    return {
        "query": query.strip(),
        "year": str(year),
        "depth": int(depth),
        "top_k": int(top_k),
        "shock_params": {
            "input_type": input_type,
            "price_m_change_rate": float(price_m_change_rate),
            "duration_month": int(duration_month),
        },
    }


# ── 단계 1·2·3: HS 검색 → 후보 → 선택 ─────────────────────────────────────


def step_hs_search(query: str) -> None:
    st.subheader("Step 1·2 — HS 검색 (RRF hybrid)")
    if not query:
        st.info("사이드바에 질의를 입력하세요.")
        return

    if st.button("HS 검색 실행", type="primary"):
        try:
            candidates = get_rag_client().search(query, limit=10)
        except Exception as exc:
            st.error(f"RAG 검색 실패: {exc.__class__.__name__} — {exc}")
            return
        st.session_state["hs_candidates"] = candidates
        # 새 검색이면 이후 단계 초기화
        for k in (
            "selected_hs10",
            "seed_df",
            "subgraph",
            "llm_results",
            "primary_bizno",
            "shock_result",
        ):
            st.session_state.pop(k, None)

    candidates: list[dict] = st.session_state.get("hs_candidates", [])
    if not candidates:
        return

    st.write(f"후보 {len(candidates)} 건 — score 내림차순")
    df = pd.DataFrame(candidates)
    show_cols = [c for c in ("hs_code", "name_ko", "name_en", "score") if c in df.columns]
    st.dataframe(df[show_cols].style.format({"score": "{:.4f}"}))

    st.subheader("Step 3 — HS 한 건 선택")
    hs_options = df["hs_code"].astype(str).tolist()
    selected_hs10 = st.selectbox(
        "HS Code (10자리)",
        hs_options,
        index=0,
        help="여기서 선택한 HS 가 s_ra603 시드 추출의 키가 됨",
    )
    st.session_state["selected_hs10"] = selected_hs10


# ── 단계 4: s_ra603 시드 추출 ─────────────────────────────────────────────


def step_seeds(year: str) -> None:
    hs10 = st.session_state.get("selected_hs10")
    if not hs10:
        return

    st.subheader(f"Step 4 — 시드 업체 추출 (kis_ra.s_ra603, HS={hs10}, year={year})")
    if st.button("시드 추출"):
        try:
            seed_df = s_ra603.fetch_seeds(hs_code10=hs10, trade_year=year)
        except Exception as exc:
            st.error(
                f"s_ra603 조회 실패 — 컬럼 매핑이 운영 스키마와 다를 수 있음. "
                f"환경변수 KIS_RA604_* 로 override 가능. ({exc.__class__.__name__}: {exc})"
            )
            return
        st.session_state["seed_df"] = seed_df
        for k in (
            "subgraph",
            "llm_results",
            "primary_bizno",
            "shock_result",
        ):
            st.session_state.pop(k, None)

    seed_df: pd.DataFrame | None = st.session_state.get("seed_df")
    if seed_df is None:
        return

    st.write(f"시드 업체: {len(seed_df)} 곳 (EXP+IMP 합집합)")
    if not seed_df.empty:
        view = seed_df.copy()
        view["country_mix"] = view["country_mix"].apply(
            lambda v: json.dumps(v[:3], ensure_ascii=False) if v else "[]"
        )
        st.dataframe(view)


# ── 단계 5: edge 3차 확장 그래프 ───────────────────────────────────────────


def step_expand(year: str, depth: int, top_k: int) -> None:
    seed_df: pd.DataFrame | None = st.session_state.get("seed_df")
    if seed_df is None or seed_df.empty:
        return

    st.subheader(f"Step 5 — 3차 확장 (depth={depth}, top_k={top_k})")
    if st.button("그래프 확장"):
        seeds = seed_df["bizno"].astype(str).tolist()
        try:
            sg = sub.expand(
                seeds, trade_year=year, depth=depth, top_k=top_k
            )
        except Exception as exc:
            st.error(f"그래프 확장 실패: {exc.__class__.__name__} — {exc}")
            return
        st.session_state["subgraph"] = sg
        for k in ("llm_results", "primary_bizno", "shock_result"):
            st.session_state.pop(k, None)

    sg: sub.Subgraph | None = st.session_state.get("subgraph")
    if sg is None:
        return

    st.write(f"노드 {len(sg.nodes)} / 엣지 {len(sg.edges)}")
    if sg.edges.empty and len(sg.nodes) <= len(seed_df):
        st.info(
            "이 시드는 현재 public.edge 데이터에 등장하지 않아 확장이 0차에서 멈췄습니다. "
            "운영 거래 데이터가 적재되면 그래프/LLM/쇼크 단계가 활성화됩니다. "
            "(현 데모 데이터의 edge 는 trade_year=2024 + 삼성전기 hub 중심 310 건 한정.)"
        )
    if len(sg.nodes) > 800:
        st.warning(
            f"노드가 {len(sg.nodes)} 개로 streamlit-agraph 렌더 한계(~500)를 넘어 "
            f"브라우저가 느려질 수 있습니다. top_k 를 낮추세요."
        )
    categories = {
        b: r.get("category", "")
        for b, r in (st.session_state.get("llm_results") or {}).items()
    }
    clicked = graph_view.draw(sg, categories=categories)
    if clicked:
        st.caption(f"선택된 노드: {clicked}")


# ── 단계 6: LLM 1차 선정 ───────────────────────────────────────────────────

_CATEGORY_DEFINITIONS = (
    "HIGH = 외생 수입 가격/공급 충격이 매출 또는 원가에 직접 30% 이상 영향. "
    "MEDIUM = 10~30%. LOW = <10%. NONE = 무관."
)


def _build_company_context(
    bizno: str,
    node_row: pd.Series,
    edge_stats: pd.Series | None,
    s_ra_row: pd.Series | None,
) -> str:
    lines = [
        f"bizno: {bizno}",
        f"기업명(KO): {node_row.get('name_ko') or '-'}",
        f"기업명(EN): {node_row.get('name_en') or '-'}",
        f"대표자: {node_row.get('rep_ko') or '-'}",
        f"hop(확장차수): {int(node_row.get('hop', -1))}",
    ]
    if edge_stats is not None:
        lines.append(
            f"고객수: {int(edge_stats.get('n_customers', 0))}, "
            f"공급사수: {int(edge_stats.get('n_suppliers', 0))}, "
            f"최대거래(out): {int(edge_stats.get('top_out_amt', 0)):,}, "
            f"최대거래(in): {int(edge_stats.get('top_in_amt', 0)):,}"
        )
    if s_ra_row is not None:
        mix = s_ra_row.get("country_mix") or []
        top = mix[:5] if isinstance(mix, list) else []
        lines.append(
            f"수입액: {int(s_ra_row.get('imp_amt') or 0):,}, "
            f"수출액: {int(s_ra_row.get('exp_amt') or 0):,}, "
            f"상위 국가비중: {json.dumps(top, ensure_ascii=False)}"
        )
    return "\n".join(lines)


def step_llm(year: str) -> None:
    sg: sub.Subgraph | None = st.session_state.get("subgraph")
    if sg is None or sg.nodes.empty:
        return

    st.subheader("Step 6 — LLM 1차 업체 선정 (Qwen2.5-7B via ollama)")
    st.caption(_CATEGORY_DEFINITIONS)

    if st.button("LLM 분류 실행"):
        biznos = sg.nodes["bizno"].astype(str).tolist()
        # 노드별 거래 집계
        agg = sub.aggregate_edge_stats(sg)
        # s_ra603 국가비중 — 노드 전체에 한 번에
        try:
            mix_df = s_ra603.fetch_company_mix(biznos, trade_year=year)
            mix_df = mix_df.set_index("bizno") if not mix_df.empty else mix_df
        except Exception as exc:
            st.warning(
                f"s_ra603 일괄 조회 실패 — country_mix 없이 진행. "
                f"({exc.__class__.__name__}: {exc})"
            )
            mix_df = pd.DataFrame()

        client = get_llm_json_client()
        results: dict[str, dict] = {}
        prog = st.progress(0.0, text="LLM 분류 진행…")
        for i, b in enumerate(biznos, 1):
            node_row = sg.nodes.set_index("bizno").loc[b]
            edge_stats = agg.loc[b] if b in agg.index else None
            s_ra_row = mix_df.loc[b] if (not mix_df.empty and b in mix_df.index) else None
            ctx = _build_company_context(b, node_row, edge_stats, s_ra_row)
            results[b] = client.classify_company(
                company_context=ctx,
                category_definitions=_CATEGORY_DEFINITIONS,
            )
            prog.progress(i / len(biznos), text=f"LLM 분류 {i}/{len(biznos)}")
        prog.empty()
        st.session_state["llm_results"] = results
        # 기본 1차 = HIGH + MEDIUM
        st.session_state["primary_bizno"] = [
            b for b, r in results.items() if r.get("category") in ("HIGH", "MEDIUM")
        ]
        st.session_state.pop("shock_result", None)

    results = st.session_state.get("llm_results")
    if not results:
        return

    df = pd.DataFrame(
        [{"bizno": b, **r} for b, r in results.items()]
    )
    df = df.merge(
        sg.nodes[["bizno", "name_ko", "hop"]], on="bizno", how="left"
    )
    cat_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "NONE": 3, "": 4}
    df["_o"] = df["category"].map(lambda c: cat_order.get(c, 5))
    df = df.sort_values(["_o", "hop"]).drop(columns="_o")

    st.dataframe(df, height=400)

    # 사용자 보정: 표 외에 직접 toggle 도 허용
    candidate_high_med = df[df["category"].isin(["HIGH", "MEDIUM"])]["bizno"].tolist()
    selected = st.multiselect(
        "1차 업체 (default = HIGH+MEDIUM, 직접 가감 가능)",
        options=df["bizno"].tolist(),
        default=st.session_state.get("primary_bizno", candidate_high_med),
    )
    st.session_state["primary_bizno"] = selected
    st.caption(f"1차 업체 수: {len(selected)}")


# ── 단계 7: 쇼크 산출 ─────────────────────────────────────────────────────


def step_shock(year: str, shock_params: dict) -> None:
    sg: sub.Subgraph | None = st.session_state.get("subgraph")
    primary: list[str] | None = st.session_state.get("primary_bizno")
    if sg is None or not primary:
        return

    st.subheader("Step 7 — Leontief 쇼크 산출")

    if st.button("쇼크 계산 실행", type="primary"):
        seed_df: pd.DataFrame = st.session_state.get("seed_df", pd.DataFrame())
        seed_meta = {}
        if not seed_df.empty:
            seed_meta = {
                row.bizno: {"exp_amt": row.exp_amt, "imp_amt": row.imp_amt}
                for row in seed_df.itertuples(index=False)
            }
        firms, edges, exports = sub.to_poc_frames(
            sg, seed_meta_by_bizno=seed_meta
        )
        result, err = shock_runner.safe_run(
            firms=firms,
            edges=edges,
            exports=exports,
            primary_bizno=primary,
            trade_year=int(year),
            shock_params=shock_params,
        )
        if err:
            st.error(f"쇼크 계산 실패: {err}")
            return
        st.session_state["shock_result"] = result

    result = st.session_state.get("shock_result")
    if result is None:
        return

    shock_view.render_diagnostics(result)
    shock_view.render_top_tis(result, top_n=20)
    shock_view.render_impact_table(result, top_n=30)


# ── 메인 ─────────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(
        page_title="NICE 데모 — 외생 충격 시나리오",
        layout="wide",
    )
    cfg = sidebar_inputs()

    step_hs_search(cfg["query"])
    step_seeds(cfg["year"])
    step_expand(cfg["year"], cfg["depth"], cfg["top_k"])
    step_llm(cfg["year"])
    step_shock(cfg["year"], cfg["shock_params"])


if __name__ == "__main__":
    main()
