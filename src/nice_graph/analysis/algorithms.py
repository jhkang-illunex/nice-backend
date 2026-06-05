"""networkx 표준 알고리즘 wrapper — 데모 뼈대.

본 모듈은 도메인 무관 — 그래프와 옵션만 받아 결과를 단순 dict/list 로 반환.
``nice_poc.propagation`` / ``matrix`` 의 도메인 특화(Leontief / BiCGStab /
spectral radius) 통합은 별 단계 (요구사항 §7~9 후속).
"""

from __future__ import annotations

import networkx as nx


# ─── 기본 통계 ─────────────────────────────────────────────────────────────


def summary(g: nx.DiGraph) -> dict[str, object]:
    n, m = g.number_of_nodes(), g.number_of_edges()
    return {
        "nodes": n,
        "edges": m,
        "density": nx.density(g),
        "weakly_connected_components": nx.number_weakly_connected_components(g),
        "strongly_connected_components": nx.number_strongly_connected_components(g),
        "is_dag": nx.is_directed_acyclic_graph(g),
    }


# ─── Centrality 3 종 ───────────────────────────────────────────────────────


def degree_centrality(g: nx.DiGraph, top_k: int = 20) -> list[dict[str, object]]:
    """in/out degree centrality. Star graph 식별에 즉시 유용."""
    in_c = nx.in_degree_centrality(g)
    out_c = nx.out_degree_centrality(g)
    rows = [
        {
            "bizno": n,
            "in": in_c.get(n, 0.0),
            "out": out_c.get(n, 0.0),
            "total": in_c.get(n, 0.0) + out_c.get(n, 0.0),
        }
        for n in g.nodes
    ]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return rows[:top_k]


def pagerank(
    g: nx.DiGraph,
    top_k: int = 20,
    *,
    alpha: float = 0.85,
    weighted: bool = True,
) -> list[dict[str, object]]:
    """가중 PageRank. weighted=False 면 unweighted."""
    pr = nx.pagerank(g, alpha=alpha, weight="weight" if weighted else None)
    items = sorted(pr.items(), key=lambda x: x[1], reverse=True)
    return [{"bizno": n, "pagerank": s} for n, s in items[:top_k]]


def betweenness(
    g: nx.DiGraph,
    top_k: int = 20,
    *,
    weighted: bool = True,
    normalized: bool = True,
) -> list[dict[str, object]]:
    """Betweenness centrality. 큰 그래프엔 비용 O(N·M) — 데모 규모(N≤1k)면 OK."""
    bc = nx.betweenness_centrality(
        g, weight="weight" if weighted else None, normalized=normalized,
    )
    items = sorted(bc.items(), key=lambda x: x[1], reverse=True)
    return [{"bizno": n, "betweenness": s} for n, s in items[:top_k]]


# ─── 경로 + 이웃 ───────────────────────────────────────────────────────────


def shortest_path(
    g: nx.DiGraph,
    source: str,
    target: str,
    *,
    weighted: bool = True,
) -> dict[str, object]:
    """Dijkstra (weighted=True 시) 또는 BFS shortest path."""
    if source not in g:
        return {"found": False, "reason": f"source {source!r} not in graph"}
    if target not in g:
        return {"found": False, "reason": f"target {target!r} not in graph"}
    try:
        path = nx.shortest_path(
            g, source=source, target=target,
            weight="weight" if weighted else None,
        )
        length = nx.shortest_path_length(
            g, source=source, target=target,
            weight="weight" if weighted else None,
        )
    except nx.NetworkXNoPath:
        return {"found": False, "reason": "no path"}
    return {
        "found": True,
        "path": list(path),
        "length": float(length),
        "hops": len(path) - 1,
    }


def neighbors(
    g: nx.DiGraph,
    bizno: str,
    depth: int = 1,
    *,
    direction: str = "both",
) -> dict[str, object]:
    """N-depth 이웃 BFS.

    Args:
      direction: 'in' (incoming), 'out' (outgoing), 'both' (무방향)
    """
    if bizno not in g:
        return {"found": False, "reason": f"bizno {bizno!r} not in graph"}

    if direction == "both":
        h = g.to_undirected(as_view=True)
    elif direction == "in":
        h = g.reverse(copy=False)
    elif direction == "out":
        h = g
    else:
        raise ValueError(f"unknown direction {direction!r}")

    bfs = nx.single_source_shortest_path_length(h, bizno, cutoff=depth)
    layers: dict[int, list[str]] = {}
    for n, d in bfs.items():
        layers.setdefault(d, []).append(n)
    return {
        "found": True,
        "source": bizno,
        "depth": depth,
        "direction": direction,
        "total_nodes": len(bfs),
        "layers": {str(k): v for k, v in sorted(layers.items())},
    }


# ─── 컴포넌트 ──────────────────────────────────────────────────────────────


def components(g: nx.DiGraph, top_k: int = 5) -> dict[str, object]:
    weak = sorted(nx.weakly_connected_components(g), key=len, reverse=True)
    strong = sorted(nx.strongly_connected_components(g), key=len, reverse=True)
    return {
        "weak_count": len(weak),
        "weak_largest_size": len(weak[0]) if weak else 0,
        "weak_top_k_sizes": [len(c) for c in weak[:top_k]],
        "strong_count": len(strong),
        "strong_largest_size": len(strong[0]) if strong else 0,
        "strong_top_k_sizes": [len(c) for c in strong[:top_k]],
    }
