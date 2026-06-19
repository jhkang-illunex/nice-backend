"""PostgreSQL → 그래프 어댑터 (네트워크 분석 계층의 데이터 로더).

운영 PG (`.env` 의 POSTGRES_*) 의 ``public.node`` / ``public.edge`` 를
**read-only SELECT** 만으로 읽어 두 가지 형식으로 반환:

  * Flatten 형식  : ``load_nodes()`` / ``load_edges()`` → pandas.DataFrame
  * 네트워크 형식 : ``build_graph()`` → networkx.DiGraph (노드 속성 + 엣지 가중치)

호출 체인
  ``api/routers/network.py`` 의 각 엔드포인트 → ``build_graph()`` → ``analysis/algorithms.py``.
  즉 본 모듈은 **/api/network/* 계열(중심성·경로·컴포넌트) 전용 데이터 진입점**이다.

스키마 가정(설계 당시)
  node : bizno (PK, 사업자번호) + 한/영 기업명/대표명 + 메타 다수
  edge : start_bizno → end_bizno + trade_year + 거래 메타(sly_amt 공급가액,
         taxbll_cnt 세금계산서수, trade_cnt 거래횟수, ...)

운영 31 테이블 무수정 — INSERT/UPDATE/DDL 없음 (read-only).

⚠️ 테이블 드리프트 (반드시 인지)
  현재 운영 스키마에서 ``public.node`` / ``public.edge`` 는 **0건(빈 테이블)** 이다.
  실제 거래 그래프 데이터는 ``public.company_edge`` (from_bizno→to_bizno, sly_amt,
  trade_year) / ``public.company`` 에 있고, **쇼크 파이프라인**(``nice_graph.shock``)은
  그쪽을 직접 읽는다. 따라서 본 모듈과 /api/network/* 는 빈 테이블을 조회하는
  **구조적 골격** 상태 — 그대로 호출하면 빈 그래프(노드/엣지 0)가 반환된다.
  이 드리프트는 프로젝트 담당 범위상 의도적으로 **수정하지 않는다**(쇼크 전파와 무관).
  실데이터로 돌리려면 SQL 의 테이블·컬럼명을 company_edge/company 계열로 매핑해야 한다.
  (참고: ``shock/fetch.py`` 는 같은 이유로 이미 company_edge 로 교정됨.)
"""

from __future__ import annotations

from typing import Literal

import networkx as nx
import pandas as pd
from sqlalchemy import text

from nice_poc.db import get_pg_engine

WeightCol = Literal["sly_amt", "trade_cnt", "taxbll_cnt", "tamt_amt", "taxfr_amt"]

_VALID_WEIGHTS: tuple[str, ...] = (
    "sly_amt", "trade_cnt", "taxbll_cnt", "tamt_amt", "taxfr_amt",
)


_NODE_SQL = text(
    """
    SELECT bizno,
           korentrnm AS name_ko,
           engentrnm AS name_en,
           korreprnm AS rep_ko
    FROM public.node
    """
)

# CAST 가 핵심 — :trade_year 가 NULL 일 때 PG 가 양쪽 모두 untyped 라 타입 추론
# 실패함. 명시적 ::text 캐스팅이면 IS NULL 비교가 안전.
_EDGE_SQL = text(
    """
    SELECT id,
           start_bizno,
           end_bizno,
           trade_year,
           taxbll_cnt,
           sly_amt,
           tamt_amt,
           taxfr_amt,
           trade_cnt
    FROM public.edge
    WHERE (CAST(:trade_year AS text) IS NULL OR trade_year = CAST(:trade_year AS text))
    """
)


def load_nodes() -> pd.DataFrame:
    """flatten 형식 — node 테이블 한 번에 DataFrame."""
    with get_pg_engine().connect() as conn:
        return pd.read_sql_query(_NODE_SQL, conn)


def load_edges(*, trade_year: str | None = None) -> pd.DataFrame:
    """flatten 형식 — edge 테이블 (trade_year 필터 옵션)."""
    with get_pg_engine().connect() as conn:
        return pd.read_sql_query(
            _EDGE_SQL, conn, params={"trade_year": trade_year}
        )


def _resolve_weight(weight: str | None) -> str | None:
    """weight 로 쓸 edge 컬럼명을 검증 (SQL 주입 방지 — 화이트리스트만 허용).

    weight 컬럼명은 build_graph 에서 ``getattr(edge_row, weight)`` 로 동적 접근하므로
    임의 문자열을 막아야 한다. None 이면 가중치 미사용(모든 엣지 weight=1.0).
    """
    if weight is None:
        return None
    if weight not in _VALID_WEIGHTS:
        raise ValueError(
            f"unknown weight column {weight!r}; allowed: {_VALID_WEIGHTS}"
        )
    return weight


def build_graph(
    *,
    trade_year: str | None = None,
    weight: WeightCol | None = "sly_amt",
) -> nx.DiGraph:
    """네트워크 형식 — networkx.DiGraph 로 반환.

    - 노드: ``bizno`` (str) + ``name_ko / name_en / rep_ko`` 속성
    - 엣지: ``(start_bizno → end_bizno)`` + 거래 메타 + ``weight`` 속성
    - ``weight`` 매핑 가능 컬럼은 ``_VALID_WEIGHTS``. None 이면 weight=1.0 통일.

    Args:
      trade_year: 적용 연도 필터 (예: "2024"). None 이면 모든 연도.
      weight: weight 로 사용할 edge 메타 컬럼.

    Returns:
      networkx.DiGraph
    """
    w = _resolve_weight(weight)

    # 노드·엣지를 각각 한 번의 SELECT 로 받아 메모리에서 그래프 조립 (호출당 풀 로드).
    nodes = load_nodes()
    edges = load_edges(trade_year=trade_year)

    g: nx.DiGraph = nx.DiGraph()
    # 1) 노드 먼저 등록 — 기업명/대표명을 노드 속성으로 (결과 표시·라벨용).
    for n in nodes.itertuples(index=False):
        g.add_node(
            n.bizno,
            name_ko=getattr(n, "name_ko", None),
            name_en=getattr(n, "name_en", None),
            rep_ko=getattr(n, "rep_ko", None),
        )

    # 2) 엣지 등록 — weight 속성은 선택 컬럼값(없거나 NaN 이면 1.0). networkx 알고리즘
    #    (pagerank/betweenness/dijkstra)이 weight='weight' 로 참조하는 표준 키로 통일.
    #    나머지 거래 메타(sly_amt 등)는 NaN→None 정규화해 부가 속성으로 보존.
    for e in edges.itertuples(index=False):
        weight_value = float(getattr(e, w)) if w and pd.notna(getattr(e, w)) else 1.0
        g.add_edge(
            e.start_bizno,
            e.end_bizno,
            weight=weight_value,
            trade_year=e.trade_year,
            taxbll_cnt=int(e.taxbll_cnt) if pd.notna(e.taxbll_cnt) else None,
            sly_amt=float(e.sly_amt) if pd.notna(e.sly_amt) else None,
            tamt_amt=float(e.tamt_amt) if pd.notna(e.tamt_amt) else None,
            taxfr_amt=float(e.taxfr_amt) if pd.notna(e.taxfr_amt) else None,
            trade_cnt=int(e.trade_cnt) if pd.notna(e.trade_cnt) else None,
        )

    return g
