"""HS → 시드 → N차 확장 그래프 조회.

스키마 의존
  public.origin_kis_em__s_em001 — 시드 추출용 (bizno + upchecd)
  public.origin_kis_ra__s_ra603 — HS×MTI 산업분류 비중 메타 (시드 join)
  public.edge                   — 거래관계 (from_bizno, to_bizno, sly_amt, trade_year)

mode 인자
  ``BFS`` / ``DFS`` 둘 다 *깊이 N 까지 도달 가능한 동일한 노드/엣지 셋* 을
  반환 (응답에 visit-order 가 없으므로 결과 set 은 알고리즘 무관).
  BFS = hop-by-hop SQL (frontier expansion). DFS = 같은 결과를 *iterative
  stack* 로 traversal — PoC 단계에서는 결과 set 만 일치하면 충분.

비율 정의
  ``all_rate``     = source 의 outgoing 행 정규화 (Σ_out = 1)
                    = sly_amt(from→to, 전 연도 합) / SUM(sly_amt(from→*, 전 연도 합))
  ``years_rate[yr]`` = source 의 연도별 outgoing 중 비중
                    = sly_amt(from→to, yr) / SUM(sly_amt(from→*, yr))
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from sqlalchemy import text

from nice_poc.db import get_pg_engine

log = logging.getLogger(__name__)

Mode = Literal["BFS", "DFS"]


# ─── 출력 스키마 (사용자 사양) ────────────────────────────────────────────


class NodeRow(TypedDict):
    bizno: str
    upchecd: str | None


class EdgeRow(TypedDict):
    from_bizno: str
    to_bizno: str
    years_rate: dict[str, float]  # {'2024': 0.6, '2025': 0.4}
    all_rate: float


@dataclass
class SubgraphResult:
    nodes: list[NodeRow] = field(default_factory=list)
    edges: list[EdgeRow] = field(default_factory=list)


# ─── SQL ──────────────────────────────────────────────────────────────────


def _hs6(hscode: str) -> str:
    """6/10 자리 입력 모두 6자리로 정규화."""
    return (hscode or "").strip()[:6]


_SEED_SQL = text(
    """
    SELECT DISTINCT em.bizno, em.upchecd
    FROM public.origin_kis_em__s_em001 em
    WHERE em.upchecd = :hs6
      AND EXISTS (
          SELECT 1 FROM public.origin_kis_ra__s_ra603 ra
          WHERE ra.upchecd = em.upchecd
      )
    """
)


_EDGES_OF_FRONTIER_SQL = text(
    """
    SELECT from_bizno,
           to_bizno,
           trade_year,
           COALESCE(sly_amt, 0)::float AS sly_amt
    FROM public.edge
    WHERE from_bizno = ANY(:frontier) OR to_bizno = ANY(:frontier)
    """
)


_NODE_UPCHECD_SQL = text(
    """
    SELECT bizno, upchecd
    FROM public.origin_kis_em__s_em001
    WHERE bizno = ANY(:biznos)
    """
)


# ─── 시드 추출 ────────────────────────────────────────────────────────────


def _fetch_seeds(hs6: str) -> list[str]:
    with get_pg_engine().connect() as c:
        rows = c.execute(_SEED_SQL, {"hs6": hs6}).fetchall()
    return [r[0] for r in rows]


def _fetch_edges_for_frontier(frontier: list[str]) -> list[tuple]:
    if not frontier:
        return []
    with get_pg_engine().connect() as c:
        return c.execute(
            _EDGES_OF_FRONTIER_SQL, {"frontier": list(frontier)}
        ).fetchall()


def _fetch_upchecd_map(biznos: list[str]) -> dict[str, str | None]:
    if not biznos:
        return {}
    with get_pg_engine().connect() as c:
        rows = c.execute(_NODE_UPCHECD_SQL, {"biznos": list(biznos)}).fetchall()
    return {r[0]: r[1] for r in rows}


# ─── 확장 (BFS / DFS) ─────────────────────────────────────────────────────


def _expand_bfs(
    seeds: list[str], n_of_child: int
) -> tuple[set[str], list[tuple]]:
    """hop-by-hop SQL — frontier 의 in/out edge 를 깊이 N 까지 모음."""
    visited: set[str] = set(seeds)
    edge_rows: list[tuple] = []
    frontier: list[str] = list(seeds)
    for _ in range(n_of_child):
        if not frontier:
            break
        rows = _fetch_edges_for_frontier(frontier)
        edge_rows.extend(rows)
        # 다음 frontier = 이번 hop 에서 새로 발견된 노드
        new_nodes = {b for r in rows for b in (r[0], r[1])} - visited
        visited |= new_nodes
        frontier = list(new_nodes)
    return visited, edge_rows


def _expand_dfs(
    seeds: list[str], n_of_child: int
) -> tuple[set[str], list[tuple]]:
    """iterative stack DFS — 같은 depth 한도, 결과 set 은 BFS 와 동일."""
    visited: set[str] = set(seeds)
    edge_rows: list[tuple] = []
    stack: list[tuple[str, int]] = [(s, 0) for s in seeds]
    while stack:
        node, depth = stack.pop()
        if depth >= n_of_child:
            continue
        rows = _fetch_edges_for_frontier([node])
        edge_rows.extend(rows)
        new_nodes = {b for r in rows for b in (r[0], r[1])} - visited
        visited |= new_nodes
        for nn in new_nodes:
            stack.append((nn, depth + 1))
    return visited, edge_rows


# ─── 비율 집계 (all_rate / years_rate) ─────────────────────────────────────


def _aggregate_edges(
    edge_rows: list[tuple], visited: set[str]
) -> list[EdgeRow]:
    """raw edge rows → (from,to) 별 EdgeRow with all_rate, years_rate.

    visited 안의 노드 사이 거래만 반환 (확장 한도 밖 edge 는 제외).
    """
    # (from, to) → year → sly_amt 합
    per_pair_year: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    # from → 전체 outgoing 합 (all_rate 분모)
    per_from_total: dict[str, float] = defaultdict(float)
    # from → year → outgoing 합 (years_rate 분모)
    per_from_year_total: dict[tuple[str, str], float] = defaultdict(float)

    for from_b, to_b, year, amt in edge_rows:
        if from_b not in visited or to_b not in visited:
            continue
        yr = str(year)
        per_pair_year[(from_b, to_b)][yr] += float(amt or 0.0)
        per_from_total[from_b] += float(amt or 0.0)
        per_from_year_total[(from_b, yr)] += float(amt or 0.0)

    out: list[EdgeRow] = []
    for (from_b, to_b), year_amts in per_pair_year.items():
        pair_total = sum(year_amts.values())
        denom_all = per_from_total.get(from_b, 0.0)
        all_rate = pair_total / denom_all if denom_all > 0 else 0.0
        years_rate: dict[str, float] = {}
        for yr, amt in year_amts.items():
            denom_yr = per_from_year_total.get((from_b, yr), 0.0)
            years_rate[yr] = amt / denom_yr if denom_yr > 0 else 0.0
        out.append(
            EdgeRow(
                from_bizno=from_b,
                to_bizno=to_b,
                years_rate=years_rate,
                all_rate=all_rate,
            )
        )
    return out


# ─── public API ───────────────────────────────────────────────────────────


def fetch_subgraph(
    hscode: str,
    *,
    n_of_child: int = 3,
    mode: Mode = "BFS",
) -> SubgraphResult:
    """HS → 시드 → N 차 확장 → nodes + edges.

    Args:
      hscode: HS 6자리 또는 10자리 digit string. 10자리면 앞 6자리 사용.
      n_of_child: N 차 확장 깊이.
      mode: 'BFS' 또는 'DFS'. 결과 set 동일, 알고리즘만 다름.

    Returns:
      SubgraphResult(nodes=[{'bizno','upchecd'}, ...],
                     edges=[{'from_bizno','to_bizno','years_rate','all_rate'}, ...])
    """
    hs6 = _hs6(hscode)
    seeds = _fetch_seeds(hs6)
    if not seeds:
        return SubgraphResult()

    if mode == "DFS":
        visited, edge_rows = _expand_dfs(seeds, n_of_child)
    else:
        visited, edge_rows = _expand_bfs(seeds, n_of_child)

    edges = _aggregate_edges(edge_rows, visited)

    upchecd_map = _fetch_upchecd_map(list(visited))
    nodes: list[NodeRow] = [
        NodeRow(bizno=b, upchecd=upchecd_map.get(b)) for b in sorted(visited)
    ]

    return SubgraphResult(nodes=nodes, edges=edges)
