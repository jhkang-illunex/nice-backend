"""부분 그래프 빌더 — 시드 bizno 에서 양방향 BFS depth=3, hop 마다 top-K edge.

PG ``public.node`` / ``public.edge`` 를 read-only SELECT 로 직접 조회.
``nice_graph.db.edge_graph.build_graph`` 가 전체 그래프 빌더라 데모처럼 작은
시드 기반 부분 확장에는 비효율 — 신규 어댑터를 둠.

또한 ``nice_poc.matrix.matrix_H.build`` 가 요구하는
``firms / edges / exports`` 형식의 DataFrame 까지 한 번에 만들어 쇼크 파이프라인에
바로 넘길 수 있게 한다.

스키마 매핑 (운영 PG: public.edge 의 실제 컬럼명 기준)
  edge.from_bizno  → source_id
  edge.to_bizno    → target_id
  edge.trade_year  → year (int)
  edge.sly_amt     → amount
  (없음)           → target_cate  (B2B dummy — matrix_H 의 B2C/GOV 제외 필터 통과)

운영 firm master 가 아직 없으므로 ``sales_year_fin`` 은 *outgoing edge amount 합* 으로
근사. 실 데이터 적재 후엔 ``_estimate_sales_from_edges`` 만 교체.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text

from nice_poc.db import get_pg_engine

log = logging.getLogger(__name__)

DEFAULT_CRI_SCORE = 5.0  # 0~10 척도 중간값 — firm master 없을 때 TIS 폴백


@dataclass
class Subgraph:
    """시드+확장 결과 묶음."""

    nodes: pd.DataFrame  # cols: bizno, name_ko, name_en, rep_ko, hop (0=시드, 1/2/3)
    edges: pd.DataFrame  # cols: source_id, target_id, year, amount, target_cate

    @property
    def biznos(self) -> list[str]:
        return self.nodes["bizno"].tolist()


def expand(
    seeds: list[str],
    *,
    trade_year: str,
    depth: int = 3,
    top_k: int = 50,
) -> Subgraph:
    """시드부터 양방향 BFS, hop 마다 sly_amt 상위 top_k edge 만 따라간다.

    Returns Subgraph(nodes, edges) — 누적 노드/엣지 집합.
    """
    if not seeds:
        return _empty()

    visited: set[str] = set(seeds)
    edges_acc: list[pd.DataFrame] = []
    hop_of: dict[str, int] = {b: 0 for b in seeds}
    frontier: list[str] = list(seeds)

    for h in range(1, depth + 1):
        if not frontier:
            break
        hop_edges = _fetch_topk_edges(frontier, trade_year, top_k)
        if hop_edges.empty:
            break
        edges_acc.append(hop_edges)
        new_nodes = (
            set(hop_edges["source_id"].tolist())
            | set(hop_edges["target_id"].tolist())
        ) - visited
        for b in new_nodes:
            hop_of[b] = h
        visited |= new_nodes
        frontier = list(new_nodes)

    edges_df = (
        pd.concat(edges_acc, ignore_index=True)
        if edges_acc
        else pd.DataFrame(columns=_EDGE_COLS)
    )
    # 누적 노드 안에서만 살아남은 edge 로 정리 + 중복 제거
    edges_df = edges_df[
        edges_df["source_id"].isin(visited) & edges_df["target_id"].isin(visited)
    ].drop_duplicates(subset=["source_id", "target_id", "year"])

    nodes_df = _fetch_node_meta(list(visited))
    nodes_df["hop"] = nodes_df["bizno"].map(hop_of).fillna(99).astype(int)
    return Subgraph(nodes=nodes_df, edges=edges_df)


_EDGE_COLS = ("source_id", "target_id", "year", "amount", "target_cate")


_TOPK_EDGE_SQL = text(
    """
    WITH e AS (
        SELECT from_bizno AS source_id,
               to_bizno   AS target_id,
               trade_year AS year,
               COALESCE(sly_amt, 0) AS amount
        FROM public.edge
        WHERE trade_year = :trade_year
          AND (from_bizno = ANY(:frontier) OR to_bizno = ANY(:frontier))
    )
    SELECT source_id, target_id, year, amount,
           'B2B'::text AS target_cate
    FROM e
    ORDER BY amount DESC NULLS LAST
    LIMIT :top_k
    """
)


def _fetch_topk_edges(
    frontier: list[str], trade_year: str, top_k: int
) -> pd.DataFrame:
    if not frontier:
        return pd.DataFrame(columns=_EDGE_COLS)
    with get_pg_engine().connect() as conn:
        df = pd.read_sql_query(
            _TOPK_EDGE_SQL,
            conn,
            params={
                "frontier": list(frontier),
                "trade_year": trade_year,
                "top_k": int(top_k),
            },
        )
    return df


_NODE_META_SQL = text(
    """
    SELECT bizno,
           korentrnm AS name_ko,
           engentrnm AS name_en,
           korreprnm AS rep_ko
    FROM public.node
    WHERE bizno = ANY(:biznos)
    """
)


def _fetch_node_meta(biznos: list[str]) -> pd.DataFrame:
    if not biznos:
        return pd.DataFrame(columns=["bizno", "name_ko", "name_en", "rep_ko"])
    with get_pg_engine().connect() as conn:
        df = pd.read_sql_query(_NODE_META_SQL, conn, params={"biznos": biznos})
    # node 테이블에 누락된 bizno 는 메타 없이라도 노드로 보존
    missing = set(biznos) - set(df["bizno"].tolist())
    if missing:
        df = pd.concat(
            [
                df,
                pd.DataFrame(
                    {
                        "bizno": list(missing),
                        "name_ko": None,
                        "name_en": None,
                        "rep_ko": None,
                    }
                ),
            ],
            ignore_index=True,
        )
    return df


def _empty() -> Subgraph:
    return Subgraph(
        nodes=pd.DataFrame(columns=["bizno", "name_ko", "name_en", "rep_ko", "hop"]),
        edges=pd.DataFrame(columns=_EDGE_COLS),
    )


# ── nice_poc.matrix / shock 어댑터 ─────────────────────────────────────────────


def to_poc_frames(
    sg: Subgraph,
    *,
    seed_meta_by_bizno: dict[str, dict] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Subgraph → ``matrix_H.build`` / ``direct_shock.compute`` 입력 3종.

    Returns
    -------
    firms : DataFrame indexed by firm_id(bizno).
            cols: sales_year_fin, cri_score, vat_fs_est_purchase
    edges : DataFrame cols: source_id, target_id, year, amount, target_cate
    exports : Series indexed by firm_id — s_ra603 의 exp_amt (없으면 0)
    """
    firm_ids = pd.Index(sg.nodes["bizno"].tolist(), name="firm_id")

    edges = sg.edges.copy()
    if not edges.empty:
        edges["year"] = pd.to_numeric(edges["year"], errors="coerce").astype("Int64")
        edges["amount"] = pd.to_numeric(edges["amount"], errors="coerce").fillna(0.0)

    sales_est = _estimate_sales_from_edges(edges, firm_ids)
    # supply 시나리오용 매입 추정 — incoming amount 합
    purchase_est = _estimate_purchase_from_edges(edges, firm_ids)

    firms = pd.DataFrame(
        {
            "sales_year_fin": sales_est,
            "cri_score": DEFAULT_CRI_SCORE,
            "vat_fs_est_purchase": purchase_est,
        },
        index=firm_ids,
    )

    seed_meta_by_bizno = seed_meta_by_bizno or {}
    exports = pd.Series(
        [float(seed_meta_by_bizno.get(b, {}).get("exp_amt", 0) or 0) for b in firm_ids],
        index=firm_ids,
        dtype="float64",
    )

    return firms, edges, exports


def _estimate_sales_from_edges(
    edges: pd.DataFrame, firm_ids: pd.Index
) -> pd.Series:
    """firm_id 별 outgoing edge amount 합 — sales_year_fin 폴백."""
    if edges.empty:
        return pd.Series(0.0, index=firm_ids, dtype="float64")
    grp = edges.groupby("source_id")["amount"].sum()
    return grp.reindex(firm_ids, fill_value=0.0).astype("float64")


def _estimate_purchase_from_edges(
    edges: pd.DataFrame, firm_ids: pd.Index
) -> pd.Series:
    """firm_id 별 incoming edge amount 합 — vat_fs_est_purchase 폴백."""
    if edges.empty:
        return pd.Series(0.0, index=firm_ids, dtype="float64")
    grp = edges.groupby("target_id")["amount"].sum()
    return grp.reindex(firm_ids, fill_value=0.0).astype("float64")


# ── LLM 입력용 노드 집계 (확장된 노드별 거래 요약) ─────────────────────────────


def aggregate_edge_stats(sg: Subgraph) -> pd.DataFrame:
    """노드별 (고객 수, 공급사 수, 상위 3 거래) — LLM 컨텍스트 작성용."""
    e = sg.edges
    if e.empty:
        return pd.DataFrame(
            {
                "n_customers": 0,
                "n_suppliers": 0,
                "top_out_amt": 0.0,
                "top_in_amt": 0.0,
            },
            index=sg.nodes["bizno"],
        )
    out_n = e.groupby("source_id")["target_id"].nunique().rename("n_customers")
    in_n = e.groupby("target_id")["source_id"].nunique().rename("n_suppliers")
    out_top = e.groupby("source_id")["amount"].max().rename("top_out_amt")
    in_top = e.groupby("target_id")["amount"].max().rename("top_in_amt")
    df = pd.concat([out_n, in_n, out_top, in_top], axis=1).fillna(0)
    df.index.name = "bizno"
    return df.reindex(sg.nodes["bizno"], fill_value=0)
