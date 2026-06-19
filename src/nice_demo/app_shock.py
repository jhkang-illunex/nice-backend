"""NICE 데모 (신규 파이프라인) — RAG → HS선택 → 1차 시드 → 3depth 그래프 → 쇼크 전파.

기동::

    streamlit run src/nice_demo/app_shock.py

레거시 ``app.py`` (LLM + Leontief, public.edge 기반) 와 달리, 본 앱은 사용자
담당 신규 파이프라인을 그대로 in-process 로 호출한다:

  1. RAG       : rag-server /api/hsk/search  (HTTP) → HS 후보
  2. 1차 시드  : nice_graph.shock.select_primary_firms  (ra603 거래구성 기반)
  3. 그래프    : nice_graph.shock.assemble_propagation_input  (company_edge 3depth, 복합키)
  4. 시나리오  : nice_graph.shock.run_tariff_shock / run_transaction_change
                 (관세 충격 / 거래 변화, 방향=매출(상류)·매입(하류), 가중치 A/B, 거래변화 g)
  5. 결과 표시 : 방향별 노드·에지(rate)·값(shock 또는 Δ) 그리드 + 네트워크 그래프

graph-analysis 서버 없이 PG 직결로 동작 (RAG 만 별도 서비스).
"""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st
from sqlalchemy import text
from streamlit_agraph import Config, Edge, Node, agraph

from nice_demo.clients import get_rag_client
from nice_graph.shock import (
    RandomOverrideSpec,
    build_primary_secondary_random_overrides,
    enumerate_primary_secondary,
    parse_node_id,
    run_scenario,
    select_primary_firms,
)
from nice_poc.db import get_pg_engine

# 기업규모 코드(scaledivcd) → 라벨 (대략). 미상은 코드 그대로.
_SCALE_LABEL = {"1": "대기업", "2": "중견기업", "3": "중소기업"}

# 방향 라벨(데모) ↔ assemble/scenario 의 Direction (★ 문서 기준).
#   매출 파급 = 매출처(고객)/하류 = downstream, 매입 파급 = 매입처(공급사)/상류 = upstream.
_DIR_MAP = {"매출 파급(하류)": "downstream", "매입 파급(상류)": "upstream"}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

_YEAR_OPTIONS = ["전체", "2026", "2025", "2024", "2023"]  # 시드(ra603 bse_yr)용
_EXIM_OPTIONS = {"전체": None, "수출입 0": "0", "수출입 3": "3"}
_TRADE_YEAR_OPTIONS = ["전체", "2026", "2024"]  # 전파 거래(company_edge.trade_year)용 — 2025 없음

_COLOR_SEED = "#E74C3C"   # 시드 (빨강)
_COLOR_HOT = "#F39C12"    # 높은 shock (주황)
_COLOR_WARM = "#F7DC6F"   # 중간 shock (노랑)
_COLOR_COLD = "#BDC3C7"   # 낮음/0 (회색)


# ── 사이드바 ──────────────────────────────────────────────────────────────────


def sidebar() -> dict:
    st.sidebar.title("NICE 외생충격 — 신규 파이프라인")
    st.sidebar.caption("RAG → HS → 1차 시드 → 3depth → 쇼크 전파")

    query = st.sidebar.text_input("RAG 질의 (한국어/영문)", value="밸브", help="HS 검색 키워드")

    st.sidebar.markdown("---")
    st.sidebar.subheader("1차 시드 (ra603)")
    year_label = st.sidebar.selectbox("기준연도", _YEAR_OPTIONS, index=0)
    exim_label = st.sidebar.selectbox("수출입 구분", list(_EXIM_OPTIONS), index=0)
    top_k = st.sidebar.slider("상위 K 기업", 1, 30, value=10)
    min_ratio = st.sidebar.slider("거래구성 비율 하한 (%)", 0.0, 100.0, value=0.0, step=5.0)

    st.sidebar.markdown("---")
    st.sidebar.subheader("그래프 / 전파")
    # ★ 기획서 누락분: 1차 시드 선택 후 '전파에 쓸 거래 데이터 연도' 선택.
    #   screen 의 '기준연도'(ra603 bse_yr)와 별개 — 전파/금액은 company_edge.trade_year 기준.
    trade_year_label = st.sidebar.selectbox(
        "거래 연도 (전파 데이터)",
        _TRADE_YEAR_OPTIONS,
        index=0,
        help="company_edge.trade_year 기준. '전체'=전 연도 합산. 데이터: 2024·2026.",
    )
    depth = st.sidebar.slider("확장 depth", 1, 6, value=3)
    damping = st.sidebar.slider("damping α (감쇠율)", 0.1, 1.0, value=0.85, step=0.05)
    within = st.sidebar.checkbox("서브그래프 내 정규화 (Σ_out=1)", value=True)
    use_score = st.sidebar.checkbox("초기충격 = 시드 score 비례", value=True)
    viz_top = st.sidebar.slider("그래프 표시 상위 N 노드 (shock)", 20, 400, value=80, step=20)

    st.sidebar.markdown("---")
    st.sidebar.subheader("시나리오 (래퍼)")
    scenario_label = st.sidebar.radio(
        "시나리오",
        ["관세 충격", "거래 변화"],
        index=0,
        help="관세=W불변·시드주입 / 거래변화=특정 거래비중 g수정 → 변화분 Δ(=수정W−원W)",
    )
    scenario = "tariff" if scenario_label == "관세 충격" else "transaction_change"
    dir_labels = st.sidebar.multiselect(
        "방향 (파급)",
        list(_DIR_MAP),
        default=list(_DIR_MAP),
        help="upstream=상류/매출(가중치 A), downstream=하류/매입(가중치 B)",
    )
    directions = [_DIR_MAP[d] for d in dir_labels] or ["upstream", "downstream"]
    weight_a = st.sidebar.slider("가중치 A — 매출/하류(매출처)", 0.05, 1.0, value=1.0, step=0.05)
    weight_b = st.sidebar.slider("가중치 B — 매입/상류(매입처)", 0.05, 1.0, value=1.0, step=0.05)
    norm_label = st.sidebar.radio(
        "정규화 기준",
        ["전파소스 (수렴보장)", "거래상대 (매출·매입 비중)"],
        index=0,
        help="source=Σ_out≤1 절대수렴 / counterparty=경제적 매출/매입 비중(수렴 약화, 발산 시 미수렴 표시)",
    )
    normalize = "source" if norm_label.startswith("전파소스") else "counterparty"

    return {
        "query": query.strip(),
        "year": None if year_label == "전체" else year_label,
        "trade_year": None if trade_year_label == "전체" else trade_year_label,
        "exim": _EXIM_OPTIONS[exim_label],
        "top_k": int(top_k),
        "min_ratio": float(min_ratio),
        "depth": int(depth),
        "damping": float(damping),
        "within": bool(within),
        "use_score": bool(use_score),
        "viz_top": int(viz_top),
        "scenario": scenario,
        "directions": directions,
        "weight_a": float(weight_a),
        "weight_b": float(weight_b),
        "normalize": normalize,
    }


# ── Step 1 — RAG 검색 + HS 선택 ──────────────────────────────────────────────


def step_rag(query: str) -> None:
    st.subheader("Step 1 — RAG HS 검색")
    col1, col2 = st.columns([3, 2])

    with col1:
        if st.button("RAG 검색 실행", type="primary", disabled=not query):
            try:
                hits = get_rag_client().search(query, limit=10)
                st.session_state["hs_candidates"] = hits
            except Exception as exc:  # noqa: BLE001
                st.session_state["hs_candidates"] = []
                st.warning(
                    f"rag-server 도달 실패 ({exc.__class__.__name__}). "
                    f"오른쪽에 HS코드를 직접 입력해 진행할 수 있습니다."
                )
            for k in ("select_result", "scenario_result", "ovr_candidates", "ovr_random"):
                st.session_state.pop(k, None)

        cands = st.session_state.get("hs_candidates", [])
        if cands:
            df = pd.DataFrame(cands)
            cols = [c for c in ("hs_code", "name_ko", "name_en", "score") if c in df.columns]
            st.dataframe(df[cols], height=240, use_container_width=True)
            hs_from_rag = st.selectbox(
                "HS 후보 선택", df["hs_code"].astype(str).tolist(), index=0
            )
        else:
            hs_from_rag = ""

    with col2:
        hs_manual = st.text_input(
            "HS코드 직접 입력 (4/6/10자리)",
            value=hs_from_rag or "8481",
            help="RAG 없이 바로 시드 추출하려면 여기에 입력. 비우면 좌측 선택값 사용.",
        )
    st.session_state["hscode"] = (hs_manual or hs_from_rag or "").strip()
    if st.session_state["hscode"]:
        st.caption(f"선택된 HS: **{st.session_state['hscode']}**")


# ── Step 2 — 1차 시드 추출 ───────────────────────────────────────────────────


def step_seeds(cfg: dict) -> None:
    hscode = st.session_state.get("hscode")
    st.subheader("Step 2 — 1차 시드 추출 (ra603 거래구성)")
    if not hscode:
        st.info("Step 1 에서 HS 를 선택/입력하세요.")
        return

    if st.button("시드 추출 실행"):
        try:
            res = select_primary_firms(
                hscode,
                year=cfg["year"],
                exim=cfg["exim"],
                top_k=cfg["top_k"],
                min_ratio=cfg["min_ratio"],
            )
            st.session_state["select_result"] = res
        except Exception as exc:  # noqa: BLE001
            st.error(f"시드 추출 실패: {exc.__class__.__name__} — {exc}")
            return
        for k in ("scenario_result", "ovr_candidates", "ovr_random"):
            st.session_state.pop(k, None)

    res = st.session_state.get("select_result")
    if res is None:
        return
    if not res.firms:
        st.warning(
            f"HS={res.hscode} 로 ra603 에서 매칭된 기업이 없습니다 "
            f"(ra603 커버리지 한계 — upchecd 42개 한정)."
        )
        return

    st.write(f"1차 기업 {len(res.firms)} 곳 (hs_digits={res.hs_digits}, score 내림차순)")
    df = pd.DataFrame(
        [
            {
                "bizno": f.bizno,
                "upchecd": f.upchecd,
                "기업명": f.korentrnm,
                "거래비율%": round(f.exposure_ratio, 2),
                "금액구간": f.amount_tier,
                "score": round(f.score, 4),
                "cells": f.n_cells,
            }
            for f in res.firms
        ]
    )
    st.dataframe(df, height=320, use_container_width=True)


# ── Step 3·4 — 시나리오 래퍼 (관세 충격 / 거래 변화) ──────────────────────────


def step_scenario(cfg: dict) -> None:
    res = st.session_state.get("select_result")
    st.subheader("Step 3·4 — 시나리오 래퍼 (관세 충격 / 거래 변화)")
    if res is None or not getattr(res, "firms", None):
        st.info("Step 2 에서 시드를 먼저 추출하세요.")
        return
    seeds = [(f.bizno, f.upchecd, f.score) for f in res.firms if f.bizno]
    if not seeds:
        st.warning("bizno 매핑된 시드가 없어 전파 불가.")
        return

    seed_pairs = [(b, u) for b, u, _ in seeds]
    seed_shock = {b: (s if cfg["use_score"] else 1.0) for b, _, s in seeds}
    seed_biznos = {b for b, _, _ in seeds}

    common = dict(
        weight_a=cfg["weight_a"],
        weight_b=cfg["weight_b"],
        directions=cfg["directions"],
        depth=cfg["depth"],
        trade_year=cfg["trade_year"],
        within_subgraph=cfg["within"],
        damping=cfg["damping"],
        normalize=cfg["normalize"],
        seed_shock=seed_shock,
    )
    st.caption(
        f"시나리오=**{cfg['scenario']}** · 방향={cfg['directions']} · "
        f"A(매출)={cfg['weight_a']} · B(매입)={cfg['weight_b']} · depth={cfg['depth']} · "
        f"damping={cfg['damping']} · 정규화={cfg['normalize']} · "
        f"거래연도={cfg['trade_year'] or '전체'}"
    )

    overrides: dict[tuple[str, str], float] = {}
    if cfg["scenario"] == "transaction_change":
        overrides = _override_editor(seed_pairs, seed_shock, seed_biznos, cfg)

    if st.button("시나리오 전파 실행", type="primary"):
        if cfg["scenario"] == "transaction_change" and not overrides:
            st.warning(
                "거래 변화: 적용할 거래쌍이 없습니다. "
                "수동이면 factor<1.0 지정, 랜덤이면 ‘랜덤 가중치 생성’을 먼저 실행하세요."
            )
            return
        try:
            sres = run_scenario(
                cfg["scenario"], seed_pairs, edge_overrides=overrides or None, **common
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"시나리오 전파 실패: {exc.__class__.__name__} — {exc}")
            return
        st.session_state["scenario_result"] = sres

    sres = st.session_state.get("scenario_result")
    if sres is None:
        return
    for w in sres.warnings:
        st.warning(w)
    if not sres.directions:
        st.info("선택된 방향이 없습니다.")
        return

    labels = [f"{d.effect_label} ({d.direction})" for d in sres.directions]
    for tab, dr in zip(st.tabs(labels), sres.directions, strict=True):
        with tab:
            _render_direction(dr, sres.scenario, cfg)


def _override_editor(
    seed_pairs: list, seed_shock: dict, seed_biznos: set[str], cfg: dict
) -> dict[tuple[str, str], float]:
    """거래 변화용 — 수동 입력 / 1차↔2차 매출·매입 랜덤 중 선택."""
    mode = st.radio(
        "거래 변화 입력 방식",
        ["랜덤 (1차↔2차 매출/매입)", "수동 입력"],
        horizontal=True,
        key="ovr_mode",
    )
    if mode.startswith("랜덤"):
        return _override_random(seed_pairs, seed_shock, seed_biznos, cfg)
    return _override_manual(seed_pairs, seed_shock, seed_biznos, cfg)


def _override_random(
    seed_pairs: list, seed_shock: dict, seed_biznos: set[str], cfg: dict
) -> dict[tuple[str, str], float]:
    """랜덤 — 1차↔2차 매출/매입 거래에 랜덤 g 자동 부여 (재현용 seed)."""
    st.markdown("**거래 변화(랜덤) — HS 연계 1차↔2차 거래의 매출/매입에 랜덤 g(0~1)**")
    c1, c2, c3 = st.columns([2, 3, 2])
    side_label = c1.selectbox("대상 거래", ["매출+매입", "매출만", "매입만"], index=0)
    side = {"매출+매입": "both", "매출만": "sales", "매입만": "purchase"}[side_label]
    lo, hi = c2.slider("랜덤 g 범위", 0.0, 1.0, value=(0.0, 1.0), step=0.05)
    use_seed = c3.checkbox("재현 시드 고정", value=True)
    seed_val = c3.number_input("seed", value=42, step=1, disabled=not use_seed)
    firm_sel = st.multiselect(
        "대상 1차 기업 (비우면 연계된 전체 1차)", sorted(seed_biznos), default=[]
    )

    if st.button("랜덤 가중치 생성", type="secondary"):
        spec = RandomOverrideSpec(
            side=side,
            low=float(lo),
            high=float(hi),
            seed=int(seed_val) if use_seed else None,
            only_firms=tuple(firm_sel) if firm_sel else None,
        )
        try:
            ov = build_primary_secondary_random_overrides(
                seed_pairs,
                spec=spec,
                depth=cfg["depth"],
                trade_year=cfg["trade_year"],
                within_subgraph=cfg["within"],
                damping=cfg["damping"],
                seed_shock=seed_shock,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"랜덤 생성 실패: {exc.__class__.__name__} — {exc}")
            return {}
        st.session_state["ovr_random"] = ov

    ov = st.session_state.get("ovr_random")
    if not ov:
        st.info("‘랜덤 가중치 생성’ 을 눌러 1차↔2차 매출/매입에 랜덤 g 를 부여하세요.")
        return {}

    rows = [
        {
            "구분": "매출" if s in seed_biznos else "매입",
            "from(셀러)": s,
            "to(바이어)": b,
            "factor(g)": g,
        }
        for (s, b), g in sorted(ov.items())
    ]
    df = pd.DataFrame(rows)
    n_sales = int((df["구분"] == "매출").sum())
    n_buy = int((df["구분"] == "매입").sum())
    st.caption(f"생성된 거래변화 {len(df)}건 — 매출 {n_sales} · 매입 {n_buy} (g∈[{lo},{hi}])")
    st.dataframe(df, height=240, use_container_width=True)
    return ov


def _override_manual(
    seed_pairs: list, seed_shock: dict, seed_biznos: set[str], cfg: dict
) -> dict[tuple[str, str], float]:
    """수동 — 1차→2차(셀러→바이어) 거래쌍을 불러와 factor(0~1) 로 비중 조정."""
    st.markdown("**거래 변화(수동) — 1차→2차(셀러→바이어) 거래쌍의 비중에 factor(0~1) 적용**")
    if st.button("거래쌍 불러오기 (downstream 기준)"):
        try:
            # 랜덤 생성기와 동일한 공유 열거 경로(매출=1차→2차)를 재사용.
            edges = enumerate_primary_secondary(
                seed_pairs,
                side="sales",
                depth=cfg["depth"],
                trade_year=cfg["trade_year"],
                within_subgraph=cfg["within"],
                damping=cfg["damping"],
                seed_shock=seed_shock,
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"거래쌍 조회 실패: {exc.__class__.__name__} — {exc}")
            return {}
        st.session_state["ovr_candidates"] = [
            {
                "from_bizno": e.from_bizno,
                "to_bizno": e.to_bizno,
                "셀러": e.from_name or e.from_bizno,
                "바이어": e.to_name or e.to_bizno,
                "기준 rate": round(e.rate, 4),
                "factor": 1.0,
            }
            for e in edges
        ]

    cands = st.session_state.get("ovr_candidates")
    if not cands:
        st.info("‘거래쌍 불러오기’ 를 눌러 1차→2차 거래쌍을 가져온 뒤 factor 를 조정하세요.")
        return {}

    edited = st.data_editor(
        pd.DataFrame(cands),
        height=260,
        use_container_width=True,
        column_config={
            "factor": st.column_config.NumberColumn(
                "factor (0~1)", min_value=0.0, max_value=1.0, step=0.05,
                help="이 거래 비중에 곱할 인자. 1.0=변화없음, 0.5=절반으로 축소.",
            )
        },
        disabled=["from_bizno", "to_bizno", "셀러", "바이어", "기준 rate"],
        key="ovr_editor",
    )
    overrides: dict[tuple[str, str], float] = {}
    for _, r in edited.iterrows():
        f = float(r["factor"])
        if f < 1.0:  # 변화 있는 쌍만
            overrides[(str(r["from_bizno"]), str(r["to_bizno"]))] = f
    if overrides:
        st.caption(f"변경 대상 {len(overrides)} 쌍 (factor<1.0) → 변화분 Δ 계산에 반영.")
    return overrides


# ── 렌더 헬퍼 ─────────────────────────────────────────────────────────────────


def _color_for(node_id: str, shock_val: float, hi: float, is_seed: bool) -> str:
    if is_seed:
        return _COLOR_SEED
    if hi <= 0 or shock_val <= 0:
        return _COLOR_COLD
    frac = shock_val / hi
    if frac >= 0.5:
        return _COLOR_HOT
    if frac >= 0.15:
        return _COLOR_WARM
    return _COLOR_COLD


def _render_graph(asm, shock_by_node: dict[str, float], *, top_n: int) -> None:
    idx = asm.node_index()
    # Δ(거래변화)는 음수 가능 → 크기/순위는 절대값 기준으로 안정화.
    hi = max((abs(v) for v in shock_by_node.values()), default=0.0)

    # 표시 노드 = 시드 ∪ |shock| 상위 top_n
    seed_ids = {n.node_id for n in asm.nodes if n.is_seed}
    ranked = sorted(shock_by_node.items(), key=lambda kv: abs(kv[1]), reverse=True)
    show_ids = set(seed_ids)
    for nid, _ in ranked:
        if len(show_ids) >= top_n:
            break
        show_ids.add(nid)

    if len(asm.nodes) > top_n:
        st.caption(f"노드 {len(asm.nodes)}개 중 shock 상위 {len(show_ids)}개만 표시 (시드 포함).")

    nodes: list[Node] = []
    for nid in show_ids:
        n = idx.get(nid)
        if n is None:
            continue
        sv = shock_by_node.get(nid, 0.0)
        label = f"{n.korentrnm or n.bizno}\n({n.bizno})"
        nodes.append(
            Node(
                id=nid,
                label=label,
                size=24 if n.is_seed else 12 + 12 * (abs(sv) / hi if hi > 0 else 0),
                color=_color_for(nid, sv, hi, n.is_seed),
                title=f"{n.korentrnm or '-'} | bizno={n.bizno} | upchecd={n.upchecd} | shock={sv:.4f}{' [SEED]' if n.is_seed else ''}",
            )
        )

    edges: list[Edge] = [
        Edge(source=e["from_bizno"], target=e["to_bizno"], color="#cfd6da")
        for e in asm.edges
        if e["from_bizno"] in show_ids and e["to_bizno"] in show_ids
    ]
    config = Config(
        height=640, width=1100, directed=True, physics=True,
        nodeHighlightBehavior=True, node={"labelProperty": "label", "renderLabel": True},
    )
    clicked = agraph(nodes=nodes, edges=edges, config=config)
    if clicked:
        n = idx.get(clicked)
        if n:
            st.caption(f"선택: {n.korentrnm} | bizno={n.bizno} | upchecd={n.upchecd} | shock={shock_by_node.get(clicked, 0.0):.4f}")


def _render_direction(dr, scenario: str, cfg: dict) -> None:
    """한 방향(매출/매입)의 노드·에지·값 그리드 + 네트워크 그래프."""
    asm = dr.assembled
    shock_by_node = {r["bizno"]: r["shock"] for r in dr.result.shock_list}
    val_label = "변화분Δ" if scenario == "transaction_change" else "shock"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("노드", len(asm.nodes))
    c2.metric("엣지", len(asm.edges))
    c3.metric("반복(iter)", dr.result.iterations)
    c4.metric("수렴", "✅" if dr.result.converged else "❌ ρ≥α?")
    c5.metric(f"Σ {val_label}", f"{dr.result.total_shock:.3f}")
    st.caption(
        f"effect={dr.effect_label} · direction={asm.direction} · weight={dr.weight} · "
        f"rate_kind={asm.rate_kind} · within_subgraph={asm.within_subgraph} · damping={asm.damping}"
    )
    for w in asm.warnings:
        st.warning(w)

    tab_g, tab_n, tab_e, tab_r, tab_amt = st.tabs(
        ["네트워크 그래프", "노드 그리드", "엣지 그리드 (rate)", f"{val_label} 결과", "금액 결과표(STEP6)"]
    )
    with tab_g:
        _render_graph(asm, shock_by_node, top_n=cfg["viz_top"])
    with tab_n:
        _render_nodes_grid(asm, shock_by_node, val_label)
    with tab_e:
        _render_edges_grid(asm)
    with tab_r:
        _render_result_grid(asm, shock_by_node, val_label, dr)
    with tab_amt:
        _render_amount_grid(asm, shock_by_node, val_label, cfg)


def _dl(df: pd.DataFrame, label: str, fname: str, key: str) -> None:
    st.download_button(
        label, df.to_csv(index=False).encode("utf-8-sig"),
        file_name=fname, mime="text/csv", key=key,
    )


@st.cache_data(show_spinner=False)
def _fetch_firm_amounts(biznos: tuple, trade_year: str | None) -> dict:
    """기업별 기준 매출액(Σ_out sly_amt)·매입액(Σ_in)·기업규모·공공유형 (문서 STEP6 금액표용).

    company_edge 에서 방향별 거래액 합, em001 에서 scaledivcd(규모)·eprmdydivcd(='2'→공공).
    """
    bl = list(biznos)
    if not bl:
        return {}
    yr = "AND CAST(trade_year AS text) = :yr" if trade_year else ""
    params: dict = {"b": bl}
    if trade_year:
        params["yr"] = str(trade_year)
    sales_sql = text(
        f"SELECT from_bizno b, COALESCE(SUM(sly_amt),0)::float v FROM public.company_edge "
        f"WHERE from_bizno = ANY(:b) {yr} GROUP BY from_bizno"
    )
    buy_sql = text(
        f"SELECT to_bizno b, COALESCE(SUM(sly_amt),0)::float v FROM public.company_edge "
        f"WHERE to_bizno = ANY(:b) {yr} GROUP BY to_bizno"
    )
    attr_sql = text(
        "SELECT bizno, scaledivcd, eprmdydivcd FROM public.origin_kis_em__s_em001 "
        "WHERE bizno = ANY(:b)"
    )
    out = {b: {"sales": 0.0, "buy": 0.0, "scale": None, "public": False} for b in bl}
    with get_pg_engine().connect() as c:
        for r in c.execute(sales_sql, params).mappings():
            out[r["b"]]["sales"] = r["v"]
        for r in c.execute(buy_sql, params).mappings():
            out[r["b"]]["buy"] = r["v"]
        for r in c.execute(attr_sql, {"b": bl}).mappings():
            o = out.get(r["bizno"])
            if o:
                o["scale"] = r["scaledivcd"]
                o["public"] = str(r["eprmdydivcd"]).strip() == "2"
    return out


def _render_amount_grid(asm, shock_by_node: dict[str, float], val_label: str, cfg: dict) -> None:
    """문서 STEP6 금액 결과표 — 기준 거래액 × 변화율(shock/Δ) → 변화액(원) + 기업속성.

    ※ 기능 실증용: shock/Δ 를 변화율(분수)로 해석. 실 충격강도(%) 입력은 본 프론트(Spring)에서.
    """
    biznos = tuple(sorted({n.bizno for n in asm.nodes}))
    try:
        amt = _fetch_firm_amounts(biznos, cfg.get("trade_year"))
    except Exception as exc:  # noqa: BLE001
        st.error(f"기준 거래액 조회 실패: {exc.__class__.__name__} — {exc}")
        return
    rows = []
    for n in asm.nodes:
        rate = shock_by_node.get(n.node_id, 0.0)  # 변화율(분수)로 해석
        a = amt.get(n.bizno, {})
        bs, bb = a.get("sales", 0.0), a.get("buy", 0.0)
        rows.append(
            {
                "구분": "1차" if n.is_seed else "2차",
                "기업명": n.korentrnm or n.bizno,
                "사업자번호": n.bizno,
                "기업규모": _SCALE_LABEL.get(str(a.get("scale")), a.get("scale") or "-"),
                "기업유형": "공공" if a.get("public") else "일반",
                "매출액(기준)": round(bs),
                "매출변화": round(bs * rate),
                "매출액(결과)": round(bs * (1 + rate)),
                "매출변화율": f"{rate * 100:.1f}%",
                "매입액(기준)": round(bb),
                "매입변화": round(bb * rate),
                "매입액(결과)": round(bb * (1 + rate)),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("표시할 기업이 없습니다.")
        return
    df = df.sort_values("매출변화", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)
    tot = int(df["매출변화"].sum() + df["매입변화"].sum())
    n_aff = int(((df["매출변화"].abs() + df["매입변화"].abs()) > 0).sum())
    c1, c2 = st.columns(2)
    c1.metric("총 파급효과(원)", f"{tot:,}")
    c2.metric("영향 기업 수", f"{n_aff}")
    st.caption(
        f"기준 매출액=Σ_out(sly_amt)·매입액=Σ_in · 변화액=기준×{val_label}(변화율 해석) · "
        "기업규모/유형=em001. ※ 기능 실증용(실 충격강도 입력은 Spring 프론트)."
    )
    st.dataframe(df, height=420, use_container_width=True)
    _dl(df, "금액 결과표 CSV", f"amount_{asm.direction}.csv", f"dl_amt_{asm.direction}")


def _render_nodes_grid(asm, shock_by_node: dict[str, float], val_label: str) -> None:
    """노드 그리드 — 복합키·기업명·시드·초기충격 + 래퍼 반영 결과값."""
    rows = [
        {
            "node_id": n.node_id,
            "bizno": n.bizno,
            "upchecd": n.upchecd,
            "기업명": n.korentrnm,
            "시드": "Y" if n.is_seed else "",
            "seed_shock": round(n.seed_shock, 6),
            val_label: round(shock_by_node.get(n.node_id, 0.0), 6),
        }
        for n in asm.nodes
    ]
    df = pd.DataFrame(rows).sort_values(val_label, ascending=False).reset_index(drop=True)
    st.write(f"노드 {len(df)}개 — {val_label} 내림차순")
    st.dataframe(df, height=420, use_container_width=True)
    _dl(df, "노드 CSV", f"nodes_{asm.direction}.csv", f"dl_nodes_{asm.direction}")


def _render_edges_grid(asm) -> None:
    """엣지 그리드 — 방향·가중치(A/B)·g 가 반영된 rate (전파에 실제 투입된 값)."""
    idx = asm.node_index()
    rows = []
    for e in asm.edges:
        fn, tn = idx.get(e["from_bizno"]), idx.get(e["to_bizno"])
        rows.append(
            {
                "from": e["from_bizno"],
                "from_명": fn.korentrnm if fn else None,
                "to": e["to_bizno"],
                "to_명": tn.korentrnm if tn else None,
                "rate": round(e["rate"], 6),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("rate", ascending=False).reset_index(drop=True)
    st.write(f"엣지 {len(df)}개 — rate(방향·A/B·g 반영) 내림차순")
    st.dataframe(df, height=420, use_container_width=True)
    _dl(df, "엣지 CSV", f"edges_{asm.direction}.csv", f"dl_edges_{asm.direction}")


def _render_result_grid(asm, shock_by_node: dict[str, float], val_label: str, dr) -> None:
    """값 그리드 — 노드별 전파 결과(또는 거래변화 Δ)."""
    idx = asm.node_index()
    rows = []
    for nid, sv in shock_by_node.items():
        bizno, upchecd = parse_node_id(nid)
        n = idx.get(nid)
        rows.append(
            {
                "bizno": bizno,
                "upchecd": upchecd,
                "기업명": n.korentrnm if n else None,
                val_label: round(sv, 6),
                "시드": "Y" if (n and n.is_seed) else "",
            }
        )
    df = pd.DataFrame(rows).sort_values(val_label, ascending=False).reset_index(drop=True)
    st.write(f"{val_label} 노드 {len(df)}곳 · Σ={dr.result.total_shock:.4f}")
    st.dataframe(df, height=420, use_container_width=True)
    _dl(df, f"{val_label} CSV", f"result_{asm.direction}.csv", f"dl_res_{asm.direction}")


# ── 메인 ─────────────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(page_title="NICE 외생충격 — 신규 파이프라인", layout="wide")
    cfg = sidebar()
    step_rag(cfg["query"])
    st.markdown("---")
    step_seeds(cfg)
    st.markdown("---")
    step_scenario(cfg)


if __name__ == "__main__":
    main()
