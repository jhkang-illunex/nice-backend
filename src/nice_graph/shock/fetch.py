"""HS → 시드 → N차 확장 그래프 조회.

스키마 의존
  public.origin_kis_em__s_em001 — 시드 추출용 (bizno + upchecd)
  public.origin_kis_ra__s_ra603 — HS×MTI 산업분류 비중 메타 (시드 join)
  public.company_edge           — 거래관계 (from_bizno, to_bizno, sly_amt, trade_year)

확장 방식
  재귀 CTE 로 seeds 에서 깊이 N 까지 도달한 노드의 *유도 부분그래프* (도달 노드
  사이의 모든 엣지) 를 한 쿼리에 조회. 같은 레벨 노드끼리의 수평 엣지까지
  포함되고, GROUP BY 로 중복행이 합산된다.
  ``mode`` 인자는 하위호환용 — 결과가 traversal 무관이라 값은 무시된다.

비율 정의 (서브그래프 내 정규화)
  ``all_rate``     = source 의 outgoing 행 정규화 (서브그래프 내 Σ_out = 1)
                    = sly_amt(from→to, 전 연도 합) / SUM(sly_amt(from→*, 전 연도 합))
  ``years_rate[yr]`` = source 의 연도별 outgoing 중 비중
                    = sly_amt(from→to, yr) / SUM(sly_amt(from→*, yr))
  주의 — Σ_out = 1 (ρ=1) 이라 그래프에 사이클이 있으면 propagate_shock 의
  거듭제곱급수가 발산한다. 전파에 쓸 땐 assemble.assemble_propagation_input
  의 damping(α<1) 을 거쳐 ρ ≤ α < 1 로 만들 것.
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


# 순회(depth 확장) + 유도 부분그래프 + 연도별 합산을 **재귀 CTE 한 쿼리**로.
#   reach   : seeds 에서 무방향(from/to 양쪽) 으로 depth 까지 도달한 노드.
#   결과     : 도달 노드 *사이의 모든* 엣지를 (from,to,trade_year) 별 sly_amt 합으로.
# 홉-바이-홉 BFS 가 놓치던 '같은 레벨 노드끼리의 수평 엣지' 까지 정확히 포함되고,
# GROUP BY 로 중복행이 자동 합산된다 (기존 Python BFS 의 누락·중복 버그 동시 해소).
_INDUCED_SUBGRAPH_SQL = text(
    """
    WITH RECURSIVE reach(bizno, depth) AS (
            SELECT x, 0 FROM unnest(CAST(:seeds AS text[])) AS x
        UNION
            SELECT CASE WHEN e.from_bizno = r.bizno THEN e.to_bizno ELSE e.from_bizno END,
                   r.depth + 1
            FROM reach r
            JOIN public.company_edge e
              ON (e.from_bizno = r.bizno OR e.to_bizno = r.bizno)
            WHERE r.depth < :depth
              AND e.from_bizno IS NOT NULL AND e.to_bizno IS NOT NULL
    ),
    nodes AS (SELECT DISTINCT bizno FROM reach)
    SELECT e.from_bizno,
           e.to_bizno,
           e.trade_year,
           COALESCE(SUM(e.sly_amt), 0)::float AS sly_amt
    FROM public.company_edge e
    WHERE e.from_bizno IN (SELECT bizno FROM nodes)
      AND e.to_bizno   IN (SELECT bizno FROM nodes)
    GROUP BY e.from_bizno, e.to_bizno, e.trade_year
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


def _fetch_induced_subgraph(
    seeds: list[str], n_of_child: int
) -> tuple[set[str], list[tuple]]:
    """seeds → depth N 도달 노드의 유도 부분그래프.

    Returns:
      (visited, edge_rows) — edge_rows 는 (from_bizno, to_bizno, trade_year, sly_amt)
      로 (from,to,year) 별 합산됨. visited = seeds ∪ 모든 엣지 양끝.
    """
    if not seeds:
        return set(), []
    with get_pg_engine().connect() as c:
        rows = c.execute(
            _INDUCED_SUBGRAPH_SQL, {"seeds": list(seeds), "depth": int(n_of_child)}
        ).fetchall()
    visited: set[str] = set(seeds)
    edge_rows: list[tuple] = []
    for from_b, to_b, year, amt in rows:
        visited.add(from_b)
        visited.add(to_b)
        edge_rows.append((from_b, to_b, year, amt))
    return visited, edge_rows


def _fetch_upchecd_map(biznos: list[str]) -> dict[str, str | None]:
    if not biznos:
        return {}
    with get_pg_engine().connect() as c:
        rows = c.execute(_NODE_UPCHECD_SQL, {"biznos": list(biznos)}).fetchall()
    return {r[0]: r[1] for r in rows}


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
      mode: 하위호환용 인자 — 순회를 재귀 CTE 의 유도 부분그래프로 처리하므로
            BFS/DFS 무관하게 동일 결과. (값은 무시됨.)

    Returns:
      SubgraphResult(nodes=[{'bizno','upchecd'}, ...],
                     edges=[{'from_bizno','to_bizno','years_rate','all_rate'}, ...])
    """
    del mode  # 재귀 CTE 는 traversal 무관 — 인자만 보존
    hs6 = _hs6(hscode)
    seeds = _fetch_seeds(hs6)
    if not seeds:
        return SubgraphResult()

    visited, edge_rows = _fetch_induced_subgraph(seeds, n_of_child)
    edges = _aggregate_edges(edge_rows, visited)

    upchecd_map = _fetch_upchecd_map(list(visited))
    nodes: list[NodeRow] = [
        NodeRow(bizno=b, upchecd=upchecd_map.get(b)) for b in sorted(visited)
    ]

    return SubgraphResult(nodes=nodes, edges=edges)
