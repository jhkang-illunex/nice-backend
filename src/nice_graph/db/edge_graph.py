"""PostgreSQL → 그래프 어댑터.

운영 PG (`.env` 의 POSTGRES_*) 의 ``public.node`` / ``public.edge`` 를
**read-only SELECT** 만으로 읽어 두 가지 형식으로 반환:

  * Flatten 형식  : ``load_nodes()`` / ``load_edges()`` → pandas.DataFrame
  * 네트워크 형식 : ``build_graph()`` → networkx.DiGraph (노드 속성 + 엣지 가중치)

스키마 가정(현 운영):
  node : bizno (PK, 사업자번호) + 한/영 기업명/대표명 + 메타 다수
  edge : start_bizno → end_bizno + trade_year + 거래 메타(sly_amt 공급가액,
         taxbll_cnt 세금계산서수, trade_cnt 거래횟수, ...)

운영 31 테이블 무수정 — INSERT/UPDATE/DDL 없음.
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

    nodes = load_nodes()
    edges = load_edges(trade_year=trade_year)

    g: nx.DiGraph = nx.DiGraph()
    for n in nodes.itertuples(index=False):
        g.add_node(
            n.bizno,
            name_ko=getattr(n, "name_ko", None),
            name_en=getattr(n, "name_en", None),
            rep_ko=getattr(n, "rep_ko", None),
        )

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
