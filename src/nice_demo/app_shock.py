"""NICE 데모 (신규 파이프라인) — RAG → HS선택 → 1차 시드 → 3depth 그래프 → 쇼크 전파.

기동::

    streamlit run src/nice_demo/app_shock.py

레거시 ``app.py`` (LLM + Leontief, public.edge 기반) 와 달리, 본 앱은 사용자
담당 신규 파이프라인을 그대로 in-process 로 호출한다:

  1. RAG       : rag-server /api/hsk/search  (HTTP) → HS 후보
  2. 1차 시드  : nice_dbtool.select_primary_firms  (ra603 거래구성 기반)
  3. 그래프    : nice_dbtool.assemble_propagation_input  (company_edge N-depth, 복합키, 방향별 정향)
  4. 전파      : **공개 API** /api/shock/tariff·/volume  (HTTP) — pin·init·전파·depth·volume(1+δ) 내부
  5. 결과 표시 : 방향별 노드·에지(rate)·값(shock 또는 Δ) 그리드 + 네트워크 그래프

그래프 조립은 PG 직결(nice_dbtool), 전파는 공개 shock API 에 위임 (RAG 도 별도 서비스).
"""

from __future__ import annotations

import functools
import json
import logging
import os

import httpx
import pandas as pd
import streamlit as st
from sqlalchemy import text
from streamlit_agraph import Config, Edge, Node, agraph

from nice_common.db import get_pg_engine
from nice_dbtool import (
    DirectionResult,
    ScenarioResult,
    assemble_propagation_input,
    parse_node_id,
    select_primary_firms,
)
from nice_dbtool.scenario import EFFECT_LABEL
from nice_demo.clients import get_rag_client
from nice_shock.engine import ShockResult

# 쇼크 전파 서버(HTTP) — 데모는 **공개 엔드포인트**(/api/shock/tariff·/volume)를 호출한다.
#   데모가 nice_dbtool 로 그래프를 조립·정향(triple_list)하고, pin·init·전파·depth 는
#   공개 API 가 내부 처리. 외부 클라이언트와 동일한 계약을 실제로 태우는 쇼케이스/통합테스트.
#   compose 내부: http://shock-server:8000, 로컬: http://localhost:8004
SHOCK_API_URL = os.getenv("SHOCK_API_URL", "http://localhost:8004")


def _post_shock(path: str, payload: dict) -> dict:
    """공개 shock API(path) 호출 → DataResponse(dict). 원본 응답은 화면 점검용으로 stash."""
    r = httpx.post(f"{SHOCK_API_URL}{path}", json=payload, timeout=60.0)
    r.raise_for_status()
    d = r.json()
    st.session_state.setdefault("_shock_raw", []).append(
        {"path": path, "req_edges": len(payload["triple_list"]),
         "req_seeds": len(payload["seed_list"]), "resp": d}
    )
    return d


def _data_to_result(d: dict) -> ShockResult:
    """공개 API DataResponse(data_list) → 데모 표시용 ShockResult.

    공개 API 는 converged/iterations/damped_cycles 를 숨기므로(간소화), 표시용으로는
    converged=True·iterations=0·damped_cycles=[] 로 채운다(서버가 조건부 damping 으로
    발산 순환을 이미 처리). depth 는 shock_list 항목에 실어 결과 그리드에서 surface.
    """
    return ShockResult(
        shock_list=[
            {"bizno": x["node_id"], "shock": x["shock"], "depth": x.get("depth")}
            for x in d["data_list"]
        ],
        total_shock=d["total_shock"],
        iterations=0,
        converged=True,
        damped_cycles=[],
    )

# 기업규모 코드(scaledivcd) → 라벨 (대략). 미상은 코드 그대로.
_SCALE_LABEL = {"1": "대기업", "2": "중견기업", "3": "중소기업"}

# 공공기관 상세유형(eprdtldivcd) → 라벨 (NICE 장재혁 매니저 회신 기준).
#   정부대상 매출 정의는 eprmdydivcd='2'(공공)이고, 그 안에서 4종으로 세분류된다.
_PUBLIC_DETAIL_LABEL = {"110": "공기업", "111": "준정부기관", "112": "정부기관", "119": "기타공공"}

# 방향 라벨(데모) ↔ assemble/scenario 의 Direction (★ 문서 기준).
#   매출 파급 = 매출처(고객)/하류 = downstream, 매입 파급 = 매입처(공급사)/상류 = upstream.
_DIR_MAP = {"매출 파급(하류)": "downstream", "매입 파급(상류)": "upstream"}

# 명명 시나리오 프리셋 — 선택 시 scenario·방향(·volume side) 자동 구성.
#   side 가 있으면 volume(거래량 변동): 1차의 그 side 전체 거래에 증감율 적용(firm_specs).
_SCENARIO_PRESETS: dict[str, dict | None] = {
    "① 매입 충격": {"scenario": "tariff", "directions": ["upstream"]},
    "② 매출 충격": {"scenario": "tariff", "directions": ["downstream"]},
    "③ 매출 변동(volume)": {"scenario": "volume", "directions": ["downstream"], "side": "sales"},
    "④ 매입 변동(volume)": {"scenario": "volume", "directions": ["upstream"], "side": "purchase"},
    "사용자 정의": None,
}

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
    shock_rate = st.sidebar.slider(
        "충격 비율 shock_rate (전 시드 공통)", 0.0, 1.0, value=0.1, step=0.05,
        help="영향 받는 비중 (0~1 강제). 0 은 사용 불가. "
        "tariff 주입액 = total_amount × rate(backend 조회, 0~1) × shock_rate.",
    )
    if shock_rate == 0.0:
        st.sidebar.error("충격 비율은 0 을 쓸 수 없습니다 — 0~1 범위에서 0 이 아닌 값을 선택하세요.")
        st.stop()
    st.sidebar.caption(
        f"전파는 공개 API({SHOCK_API_URL})에 위임 — 정규화=전파소스(Σ_out=1)·시드 고정(pin)·"
        "조건부 damping(ρ≥1 순환)·depth 산출 모두 서버 내부 고정."
    )
    viz_top = st.sidebar.slider("그래프 표시 상위 N 노드 (shock)", 20, 400, value=80, step=20)

    st.sidebar.markdown("---")
    st.sidebar.subheader("시나리오 (래퍼)")
    preset_label = st.sidebar.radio(
        "시나리오 선택",
        list(_SCENARIO_PRESETS),
        index=0,
        help="4개 명명 시나리오 프리셋 중 선택하거나 ‘사용자 정의’로 직접 구성.",
    )
    preset = _SCENARIO_PRESETS[preset_label]
    preset_side: str | None = None
    if preset is not None:
        scenario = preset["scenario"]
        directions = preset["directions"]
        preset_side = preset.get("side")
        st.sidebar.caption(
            f"→ `{scenario}` · 방향={directions}"
            + (" · 변동금액(원)은 본문에서 시드별 입력" if scenario == "volume" else "")
        )
    else:  # 사용자 정의 — tariff / volume 직접 구성
        scenario_label = st.sidebar.radio(
            "시나리오 유형",
            ["외생충격 (tariff)", "거래량 변동 (volume)"],
            index=0,
            help="tariff=W불변·시드 외생주입 / volume=거래량 변동(시드별 변동금액 주입·전파)",
        )
        scenario = "tariff" if scenario_label.startswith("외생충격") else "volume"
        dir_labels = st.sidebar.multiselect(
            "방향 (파급)",
            list(_DIR_MAP),
            default=list(_DIR_MAP),
            help="upstream=상류/매입, downstream=하류/매출",
        )
        directions = [_DIR_MAP[d] for d in dir_labels] or ["upstream", "downstream"]
        if scenario == "volume":
            st.sidebar.caption("거래량 변동금액(원)은 본문에서 시드 회사별로 입력합니다.")
    # 정규화/엔진/조건부 damping/pin 은 공개 API 내부 고정(전파소스·SCC·0.95·pin=True) —
    # UI 제거. 데모는 거래소스 정규화(Σ_out=1)로 조립해 triple_list 만 넘긴다.
    return {
        "query": query.strip(),
        "year": None if year_label == "전체" else year_label,
        "trade_year": None if trade_year_label == "전체" else trade_year_label,
        "exim": _EXIM_OPTIONS[exim_label],
        "top_k": int(top_k),
        "min_ratio": float(min_ratio),
        "depth": int(depth),
        "shock_rate": float(shock_rate),
        "viz_top": int(viz_top),
        "scenario": scenario,
        "directions": directions,
        "preset_label": preset_label,
        "preset_side": preset_side,
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


# ── Step 3·4 — 시나리오 래퍼 (관세 충격 / 거래 변화) — 공개 API(/tariff·/volume) 위임 ──
#
# 데모는 nice_dbtool 로 **방향별 정향·정규화(전파소스, Σ_out=1, 무감쇠)** 서브그래프를 조립한
# 뒤, 그 (이미 정향된) 엣지를 공개 엔드포인트에 direction="export"(=뒤집기 안 함)로 보낸다.
# pin·init 주입·전파·depth·volume(1+δ)는 모두 공개 API 내부 처리 → 외부 계약을 실제로 태움.
_DIR_TO_API = {"downstream": "export", "upstream": "import"}  # (참고용 — 실제 전송은 export 고정)


def _assemble_oriented(seed_pairs, direction: str, cfg: dict, seed_shock):
    """방향 d 로 정향·전파소스 정규화된 서브그래프 조립 (표시 + triple_list 추출용)."""
    return assemble_propagation_input(
        seed_pairs,
        depth=cfg["depth"],
        trade_year=cfg["trade_year"],
        within_subgraph=False,  # 전체 Σ_out 기준 = canonical trade_rate(company_edge.trade_rate
                                # 와 동일). True 면 서브그래프 내부 재정규화로 100% 아티팩트 발생.
        damping=1.0,            # 무감쇠 — 조건부 damping 은 서버 내부
        seed_shock=seed_shock,
        direction=direction,
        direction_weight=1.0,
        normalize="source",
    )


def _run_public_scenario(cfg, seed_pairs, seed_biznos, seed_deltas, hscode) -> ScenarioResult:
    """방향별로 조립→공개 API 호출→DirectionResult 구성. ScenarioResult 반환.

    tariff: seed_list=[{seed_id, upche_cd, total_amount, hscodes(HS10)}] + bse_yr +
            전역 shock_rate(0~1) — rate(HS10 수출입 비중)는 shock 서버가 backend
            (RATE_API_URL, POST /trade/weight)로 일괄 조회,
            주입액 = total_amount × Σrate × shock_rate.
    volume: seed_list=[{seed_id, total_amount, shock_rate}] — seed_deltas({bizno: 변동비율})
            를 시드별 shock_rate(0~1) 로 전달, 주입액 = total_amount × shock_rate.
    total_amount 는 방향별 기준 총액(downstream=매출 Σ_out / upstream=매입 Σ_in)을 DB 에서 조회.
    """
    scenario = cfg["scenario"]
    out: list[DirectionResult] = []
    warnings: list[str] = []
    # tariff hscodes 는 HS10 강제 (backend /trade/weight 가 H10 만 반환) — 4/6자리
    # 입력은 zero-pad 전송. 해당 H10 행이 없으면 시드가 excluded 로 명시된다.
    hs10 = (hscode or "").ljust(10, "0")
    if scenario == "tariff" and hs10 != hscode:
        warnings.append(
            f"tariff hscodes 는 HS 10자리 강제 — '{hscode}' → '{hs10}' zero-pad 전송 "
            "(backend 에 해당 H10 실적이 없으면 시드 excluded)."
        )
    if scenario == "volume":
        warnings.append(
            "거래량 변동(volume): 시드별 변동비율(shock_rate, 0=무변화)을 seed_list 로 전달 — "
            "주입액=총액×비율(서버 계산). 결과=변동금액(원), 조정액=기준액+변동금액."
        )
    for d in cfg["directions"]:
        seed_shock = cfg["shock_rate"] if scenario == "tariff" else 0.0
        asm = _assemble_oriented(seed_pairs, d, cfg, seed_shock)
        warnings.extend(f"[{d}] {m}" for m in asm.warnings)
        triple_list = [
            {"from": e["from_bizno"], "to": e["to_bizno"], "rate": e["rate"]} for e in asm.edges
        ]
        seed_nodes = [n for n in asm.nodes if n.is_seed]
        if not triple_list:
            warnings.append(
                f"[{d}] 조립된 엣지가 없어 전파 생략 — 서버 기준으로는 시드 전원이 "
                "excluded_seeds 처리되는 입력(빈 결과)."
            )
            stub = {"direction": _DIR_TO_API[d], "total_shock": 0.0, "data_list": []}
            out.append(DirectionResult(d, EFFECT_LABEL[d], 1.0, asm, _data_to_result(stub)))
            continue
        # 방향별 기준 총액 — downstream=매출(Σ_out) / upstream=매입(Σ_in)
        side_key = "sales" if d == "downstream" else "buy"
        try:
            amt = _fetch_firm_amounts(
                tuple(sorted({n.bizno for n in seed_nodes})), cfg.get("trade_year")
            )
        except Exception as exc:  # noqa: BLE001
            amt = {}
            warnings.append(
                f"[{d}] 기준 총액 조회 실패({exc.__class__.__name__}) — total_amount=0 전송"
            )
        totals = {
            n.node_id: float((amt.get(n.bizno) or {}).get(side_key, 0.0)) for n in seed_nodes
        }
        if scenario == "tariff":
            seed_payload = [
                {"seed_id": n.node_id, "upche_cd": n.upchecd or "",
                 "total_amount": totals[n.node_id], "hscodes": [hs10]}
                for n in seed_nodes
            ]
            body = {
                "triple_list": triple_list, "shock_rate": float(cfg["shock_rate"]),
                "seed_list": seed_payload, "direction": "export",
            }
            if cfg.get("trade_year"):  # 기준연도 — 미지정 시 서버 기본(2025)
                body["bse_yr"] = str(cfg["trade_year"])
            d_resp = _post_shock("/api/shock/tariff", body)
        else:
            # 시드별 입력 변동비율을 shock_rate 로 전달 (미입력=0=무변화)
            seed_payload = [
                {"seed_id": n.node_id, "total_amount": totals[n.node_id],
                 "shock_rate": float(seed_deltas.get(n.bizno, 0.0))}
                for n in seed_nodes
            ]
            d_resp = _post_shock("/api/shock/volume", {
                "triple_list": triple_list, "seed_list": seed_payload, "direction": "export",
            })
        if d_resp.get("excluded_seeds"):
            warnings.append(
                f"[{d}] 전파 제외 시드: " + ", ".join(
                    f"{e['node_id']} ({e['reason']})" for e in d_resp["excluded_seeds"]
                )
            )
        out.append(DirectionResult(d, EFFECT_LABEL[d], 1.0, asm, _data_to_result(d_resp)))
    return ScenarioResult(scenario, out, warnings)


def step_scenario(cfg: dict) -> None:
    res = st.session_state.get("select_result")
    st.subheader("Step 3·4 — 시나리오 (외생충격 / 거래량 변동) · 공개 API 위임")
    if res is None or not getattr(res, "firms", None):
        st.info("Step 2 에서 시드를 먼저 추출하세요.")
        return
    seeds = [(f.bizno, f.upchecd, f.score) for f in res.firms if f.bizno]
    if not seeds:
        st.warning("bizno 매핑된 시드가 없어 전파 불가.")
        return

    seed_pairs = [(b, u) for b, u, _ in seeds]
    seed_biznos = {b for b, _, _ in seeds}

    hscode = getattr(res, "hscode", None) or st.session_state.get("hscode") or ""
    shock_desc = (
        f"충격비율={cfg['shock_rate']:.2f} (주입액=총액×rate(조회)×비율)"
        if cfg["scenario"] == "tariff"
        else "변동비율=본문 시드별 입력"
    )
    st.caption(
        f"시나리오=**{cfg['scenario']}** · 방향={cfg['directions']} · HS={hscode or '-'} · "
        f"{shock_desc} · depth={cfg['depth']} · "
        f"거래연도={cfg['trade_year'] or '전체'} · 전파=공개 API({SHOCK_API_URL}) "
        "[pin·정규화·조건부 damping·depth 내부 고정]"
    )

    # 거래량 변동(volume): 시드 회사별 변동비율(−1~1) 입력 → seed_list 의 shock_rate 로 전달
    seed_deltas: dict[str, float] = {}
    if cfg["scenario"] == "volume":
        name_by_bizno = {f.bizno: (f.korentrnm or f.bizno) for f in res.firms if f.bizno}
        side_kr = "·".join(
            ("매출" if d == "downstream" else "매입") for d in cfg["directions"]
        )
        st.markdown(f"**시드 회사별 거래량 변동 비율 입력 ({side_kr}) — 0 ~ 1, 0=무변화**")
        st.caption(
            "각 시드 회사의 거래량 변동 비율을 입력합니다. seed_list 의 shock_rate 로 전달돼 "
            "서버가 주입액=기준 총액(DB 조회)×비율 로 계산·전파합니다. (시나리오를 바꾸면 초기화)"
        )
        ordered = sorted(seed_biznos)
        ncol = min(3, len(ordered)) or 1
        cols = st.columns(ncol)
        for i, b in enumerate(ordered):
            with cols[i % ncol]:
                # 키에 preset_label 포함 → 시나리오 선택 시 입력 전체 초기화(전체 갱신)
                seed_deltas[b] = st.slider(
                    f"{name_by_bizno.get(b, b)}",
                    0.0, 1.0, value=0.0, step=0.05,
                    key=f"volrate_{cfg['preset_label']}_{b}",
                    help=f"bizno={b} · 0~1 강제, 0=무변화. 주입액=총액×비율.",
                )
        nz = {b: v for b, v in seed_deltas.items() if abs(v) > 1e-12}
        st.caption(
            f"입력된 변동 {len(nz)}/{len(ordered)}개 시드: "
            + (", ".join(f"{name_by_bizno.get(b, b)}={v:+.2f}" for b, v in nz.items())
               or "(모두 0 — 변동 없음)")
        )

    if st.button("시나리오 전파 실행", type="primary"):
        st.session_state["_shock_raw"] = []  # 이번 실행의 공개 API 원본 응답 stash 초기화
        try:
            sres = _run_public_scenario(cfg, seed_pairs, seed_biznos, seed_deltas, hscode)
        except httpx.HTTPError as exc:
            st.error(f"공개 shock API({SHOCK_API_URL}) 호출 실패: {exc.__class__.__name__} — {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            st.error(f"시나리오 전파 실패: {exc.__class__.__name__} — {exc}")
            return
        st.session_state["scenario_result"] = sres

    sres = st.session_state.get("scenario_result")
    if sres is None:
        return
    for w in sres.warnings:
        st.warning(w)

    # 공개 API 원본 응답 일부 — 출력 점검용 (DataResponse: direction·total_shock·data_list)
    raw = st.session_state.get("_shock_raw") or []
    if raw:
        with st.expander(f"🛰 공개 API 응답 (원본 JSON 일부) · {len(raw)}건"):
            for i, item in enumerate(raw):
                resp = item["resp"]
                st.caption(
                    f"[{i}] POST {SHOCK_API_URL}{item['path']} "
                    f"(요청 엣지 {item['req_edges']}·시드 {item['req_seeds']})"
                )
                st.json({
                    "direction": resp["direction"],
                    "total_shock": round(resp["total_shock"], 4),
                    "노드수": len(resp["data_list"]),
                })
                top = sorted(resp["data_list"], key=lambda x: -abs(x["shock"]))[:5]
                st.json({"data_list (|shock| 상위 5)": top})

    if not sres.directions:
        st.info("선택된 방향이 없습니다.")
        return

    labels = [f"{d.effect_label} ({d.direction})" for d in sres.directions]
    for tab, dr in zip(st.tabs(labels), sres.directions, strict=True):
        with tab:
            _render_direction(dr, sres.scenario, cfg)


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


def _connected_show(asm, shock_by_node: dict[str, float], top_n: int):
    """표시 노드 = (시드 ∪ |shock| 상위 top_n) 중 **표시 에지로 실제 연결된** 노드만.

    top_n 에 들었어도 거래상대가 top_n 밖이라 표시 그래프에서 고립되는 노드는 제거한다
    (시드는 전파 기점이라 항상 유지). 전파 결과는 정상이고, 단지 상위 N 컷으로 인한
    '에지 없는 노드' 표시를 없애는 시각화 정리.

    반환: (show set, kept_edges list).
    """
    seed_ids = {n.node_id for n in asm.nodes if n.is_seed}
    ranked = sorted(shock_by_node.items(), key=lambda kv: abs(kv[1]), reverse=True)
    show: set[str] = set(seed_ids)
    for nid, _ in ranked:
        if len(show) >= top_n:
            break
        show.add(nid)
    kept_edges = [
        e for e in asm.edges if e["from_bizno"] in show and e["to_bizno"] in show
    ]
    connected = {e["from_bizno"] for e in kept_edges} | {e["to_bizno"] for e in kept_edges}
    show = {nid for nid in show if nid in connected or nid in seed_ids}
    return show, kept_edges


def _expand_show(asm, top_n: int):
    """시드에서 전파 방향 에지를 따라 BFS 확장, 최대 top_n 노드(시드 포함).

    방향성 그래프용 — depth-N 확장 부분그래프(asm)를 **시드 기점 BFS** 로 최대 top_n
    노드까지 잘라 연결된 확장 구조로 보여준다(shock 상위 컷이 아니라 그래프 확장 순서).
    반환: (show set, kept_edges list).
    """
    adj: dict[str, list[str]] = {}
    for e in asm.edges:
        adj.setdefault(e["from_bizno"], []).append(e["to_bizno"])
    show = [n.node_id for n in asm.nodes if n.is_seed]
    seen = set(show)
    qi = 0
    while qi < len(show) and len(show) < top_n:
        cur = show[qi]
        qi += 1
        for nb in adj.get(cur, ()):
            if nb not in seen:
                seen.add(nb)
                show.append(nb)
                if len(show) >= top_n:
                    break
    show_set = set(show)
    kept_edges = [
        e for e in asm.edges if e["from_bizno"] in show_set and e["to_bizno"] in show_set
    ]
    return show_set, kept_edges


def _graph_payload(asm, shock_by_node: dict[str, float], top_n: int) -> tuple[list, list]:
    """표시 대상(시드 ∪ |shock| 상위 top_n, 고립 제거)의 vis.js 노드·엣지 dict 리스트."""
    idx = asm.node_index()
    hi = max((abs(v) for v in shock_by_node.values()), default=0.0)
    show, kept_edges = _connected_show(asm, shock_by_node, top_n)
    nodes = []
    for nid in show:
        n = idx.get(nid)
        if n is None:
            continue
        sv = shock_by_node.get(nid, 0.0)
        nodes.append({
            "id": nid,
            "label": f"{n.korentrnm or n.bizno}\n({n.bizno})",
            "size": 24 if n.is_seed else 12 + 12 * (abs(sv) / hi if hi > 0 else 0),
            "color": _color_for(nid, sv, hi, n.is_seed),
            "title": f"{n.korentrnm or '-'} | bizno={n.bizno} | upchecd={n.upchecd} | "
                     f"shock={sv:,.0f}원{' [SEED]' if n.is_seed else ''}",
        })
    edges = [
        {"from": e["from_bizno"], "to": e["to_bizno"], "label": f"{e['rate']:.3f}", "arrows": "to"}
        for e in kept_edges
    ]
    return nodes, edges


# 동봉 vis-network UMD 번들 경로 (에어갭: CDN 미사용 — 리포에 vendoring).
_VIS_JS_PATH = os.path.join(os.path.dirname(__file__), "static", "vis-network.min.js")


@functools.lru_cache(maxsize=1)
def _vis_network_js() -> str:
    """동봉된 vis-network.min.js 를 1회 읽어 캐시. 인라인 임베드용(인터넷 차단 환경 대응).

    HTML <script> 안에 그대로 넣으므로, 문자열 리터럴에 들어있을 수 있는 '</script>' 만
    '<\\/script>' 로 무력화(파서 조기 종료 방지). 파일 부재 시 빈 문자열(다운로드 HTML 만 영향).
    """
    import re

    try:
        with open(_VIS_JS_PATH, encoding="utf-8") as f:
            js = f.read()
    except OSError:
        log.warning("vendored vis-network.min.js 누락: %s", _VIS_JS_PATH)
        return ""
    # sourceMappingURL 주석 제거(상대 .map — 미동봉, DevTools 콘솔 경고 방지) + </script> 무력화.
    js = re.sub(r"//#\s*sourceMappingURL=\S+", "", js)
    return js.replace("</script>", "<\\/script>")


def _graph_html(asm, shock_by_node: dict[str, float], *, top_n: int, title: str) -> str:
    """독립 실행형 vis.js 네트워크 HTML (동봉 vis.js 인라인, 노드·엣지 임베드). 다운로드용.

    에어갭 환경에서도 열리도록 vis-network 번들을 외부 CDN 대신 HTML 안에 인라인한다.
    """
    nodes, edges = _graph_payload(asm, shock_by_node, top_n)
    nodes_json = json.dumps(nodes, ensure_ascii=False)
    edges_json = json.dumps(edges, ensure_ascii=False)
    vis_js = _vis_network_js()
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><title>{title}</title>
<script>{vis_js}</script>
<style>html,body{{margin:0;height:100%;font-family:sans-serif}}
#net{{width:100%;height:92vh;border-top:1px solid #ddd}}
#hd{{padding:8px 14px;font-size:14px;color:#2c5fa8}}</style></head>
<body><div id="hd"><b>{title}</b> · 화살표=전파 방향 · 엣지 라벨=거래 비율(rate) · 빨강=시드</div>
<div id="net"></div>
<script>
const nodes=new vis.DataSet({nodes_json});
const edges=new vis.DataSet({edges_json});
new vis.Network(document.getElementById('net'),{{nodes,edges}},{{
  physics:{{stabilization:true,barnesHut:{{springLength:140}}}},
  edges:{{font:{{size:9,color:'#c0392b'}},color:{{color:'#9bb0c9'}},smooth:{{type:'curvedCW',roundness:0.15}}}},
  nodes:{{shape:'dot',font:{{size:12}}}},
  interaction:{{hover:true,navigationButtons:true,keyboard:true}}
}});
</script></body></html>"""


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

    # 독립 실행형 HTML 덤프 (브라우저에서 열면 동일하게 인터랙티브)
    html = _graph_html(
        asm, shock_by_node, top_n=top_n,
        title=f"외생충격 그래프 — {asm.direction}",
    )
    st.download_button(
        "🌐 인터랙티브 그래프 HTML 다운로드",
        html.encode("utf-8"),
        file_name=f"shock_graph_{asm.direction}.html",
        mime="text/html",
        key=f"dl_html_{asm.direction}",
        help="vis.js 임베드 self-contained HTML. 열면 드래그·줌·클릭 그대로 동작.",
    )


def _render_graph_directed(asm, shock_by_node: dict[str, float], *, top_n: int) -> None:
    """방향성 그래프 — 화살표=전파/거래 방향, 엣지 라벨=거래 비율(rate). Graphviz.

    양방향(A↔B 상호거래)은 각자 rate 라벨이 붙은 **두 개의 별도 화살표**로 그려진다
    (vis.js 의 겹치는 두 화살표가 아니라 방향이 분명한 directed 그래프).
    """
    idx = asm.node_index()
    hi = max((abs(v) for v in shock_by_node.values()), default=0.0)
    show, kept_edges = _expand_show(asm, top_n)
    if len(asm.nodes) > top_n:
        st.caption(
            f"시드에서 depth {asm.depth} 확장 그래프 — 노드 {len(asm.nodes)}개 중 시드 기점 BFS {len(show)}개 표시. "
            "화살표=전파 방향(매출=셀러→바이어 / 매입=바이어→셀러), 엣지 라벨=거래 비율(rate), 노드=기업명+충격금액(원)."
        )

    def esc(s: str) -> str:
        return (s or "").replace("\\", "").replace('"', "'")

    dot = [
        "digraph G {",
        "  rankdir=LR;",
        '  bgcolor="white";',
        '  node [shape=box, style="rounded,filled", fontsize=10, color="#9bb0c9"];',
        '  edge [fontsize=8, color="#9bb0c9", fontcolor="#c0392b", arrowsize=0.8];',
    ]
    for nid in show:
        n = idx.get(nid)
        if n is None:
            continue
        sv = shock_by_node.get(nid, 0.0)
        col = _color_for(nid, sv, hi, n.is_seed)
        dot.append(f'  "{nid}" [label="{esc(n.korentrnm or n.bizno)}\\n{sv:+.3f}", fillcolor="{col}"];')
    for e in kept_edges:
        dot.append(f'  "{e["from_bizno"]}" -> "{e["to_bizno"]}" [label="{e["rate"]:.3f}"];')
    dot.append("}")
    st.graphviz_chart("\n".join(dot), use_container_width=True)


def _render_direction(dr, scenario: str, cfg: dict) -> None:
    """한 방향(매출/매입)의 노드·에지·값 그리드 + 네트워크 그래프."""
    asm = dr.assembled
    shock_by_node = {r["bizno"]: r["shock"] for r in dr.result.shock_list}
    val_label = "변동금액(원, 0=무변화)" if scenario == "volume" else "파급금액(원)"

    # 공개 API 는 수렴/반복/damped 진단을 숨김(간소화) — 표시용 메트릭은 그래프 규모·depth 중심.
    depths = [r.get("depth") for r in dr.result.shock_list if r.get("depth") is not None]
    max_depth = max(depths) if depths else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("노드", len(asm.nodes))
    c2.metric("엣지", len(asm.edges))
    c3.metric("최대 depth", max_depth)
    c4.metric(f"Σ {val_label}", f"{dr.result.total_shock:,.0f}")
    st.caption(
        f"effect={dr.effect_label} · direction={asm.direction}(전송 export·서버 정향) · "
        f"rate_kind={asm.rate_kind} · normalize=source(Σ_out=1) · "
        "전파/pin/depth=공개 API 내부(조건부 damping 으로 발산 순환 처리)"
    )
    for w in asm.warnings:
        st.warning(w)

    tab_dg, tab_g, tab_n, tab_e, tab_r, tab_amt = st.tabs(
        ["방향성 그래프(거래비율)", "인터랙티브 그래프", "노드 그리드",
         "엣지 그리드 (rate)", f"{val_label} 결과", "금액 결과표(STEP6)"]
    )
    with tab_dg:
        _render_graph_directed(asm, shock_by_node, top_n=cfg["viz_top"])
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
        "SELECT bizno, scaledivcd, eprmdydivcd, eprdtldivcd "
        "FROM public.origin_kis_em__s_em001 WHERE bizno = ANY(:b)"
    )
    out = {
        b: {"sales": 0.0, "buy": 0.0, "scale": None, "public": False, "public_detail": None}
        for b in bl
    }
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
                # 공공일 때만 상세유형 보존(공기업/준정부/정부기관/기타공공).
                if o["public"]:
                    o["public_detail"] = str(r["eprdtldivcd"]).strip() or None
    return out


def _render_amount_grid(asm, shock_by_node: dict[str, float], val_label: str, cfg: dict) -> None:
    """문서 STEP6 금액 결과표 — 전파된 shock(원 단위 변화액) 그대로 + 기준 거래액·기업속성.

    주입액(총액×비율)이 원 단위 금액이라 변화액=전파값 그대로 (기준액 곱셈 없음).
    변화율(%)은 방향별 기준액(downstream=매출·upstream=매입) 대비 참고 지표.
    """
    biznos = tuple(sorted({n.bizno for n in asm.nodes}))
    try:
        amt = _fetch_firm_amounts(biznos, cfg.get("trade_year"))
    except Exception as exc:  # noqa: BLE001
        st.error(f"기준 거래액 조회 실패: {exc.__class__.__name__} — {exc}")
        return
    side_sales = str(asm.direction) == "downstream"  # 매출 파급이면 매출액 기준
    base_label = "매출액(기준)" if side_sales else "매입액(기준)"
    rows = []
    for n in asm.nodes:
        sv = shock_by_node.get(n.node_id, 0.0)  # 변화액(원) — 전파값 그대로
        a = amt.get(n.bizno, {})
        bs, bb = a.get("sales", 0.0), a.get("buy", 0.0)
        base = bs if side_sales else bb
        rows.append(
            {
                "구분": "1차" if n.is_seed else "2차",
                "기업명": n.korentrnm or n.bizno,
                "사업자번호": n.bizno,
                "기업규모": _SCALE_LABEL.get(str(a.get("scale")), a.get("scale") or "-"),
                "기업유형": (
                    _PUBLIC_DETAIL_LABEL.get(a.get("public_detail"), "공공")
                    if a.get("public")
                    else "일반"
                ),
                "매출액(기준)": round(bs),
                "매입액(기준)": round(bb),
                "변화액(원)": round(sv),
                "조정액(기준+변화)": round(base + sv),
                "변화율(기준대비)": f"{sv / base * 100:.2f}%" if base else "-",
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("표시할 기업이 없습니다.")
        return
    df = df.sort_values("변화액(원)", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)
    tot = int(df["변화액(원)"].sum())
    n_aff = int((df["변화액(원)"].abs() > 0).sum())
    c1, c2 = st.columns(2)
    c1.metric("총 파급효과(원)", f"{tot:,}")
    c2.metric("영향 기업 수", f"{n_aff}")
    st.caption(
        f"변화액=전파된 {val_label} 그대로(원 단위 직접 전파, 기준액 곱셈 없음) · "
        f"조정액·변화율 기준={base_label}(방향별: downstream=매출·upstream=매입, "
        "매출액=Σ_out(sly_amt)·매입액=Σ_in) · 기업규모(scaledivcd)/유형=em001 · "
        "공공은 eprdtldivcd로 공기업·준정부·정부기관·기타공공 세분류."
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
    st.write(f"{val_label} 노드 {len(df)}곳 · Σ={dr.result.total_shock:,.0f}원")
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
