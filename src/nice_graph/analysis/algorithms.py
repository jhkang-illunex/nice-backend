"""networkx 표준 그래프 분석 알고리즘 wrapper — 네트워크 분석 계층의 계산부.

본 모듈은 **도메인 무관**이다 — ``networkx.DiGraph`` 와 옵션만 받아, 결과를
JSON 직렬화 가능한 **순수 dict/list** 로 반환한다(FastAPI 응답에 그대로 실림).
DB·HTTP·도메인 지식 없음 → 단위 테스트가 쉽고, 그래프 출처와 분리된다.

호출자 (caller)
  ``api/routers/network.py`` 의 엔드포인트들이 ``edge_graph.build_graph()`` 로
  만든 그래프를 넘겨 호출한다. 그 외 직접 호출자는 없다(라이브러리로도 사용 가능).

  /api/network/summary               → summary()
  /api/network/centrality/degree     → degree_centrality()
  /api/network/centrality/pagerank   → pagerank()
  /api/network/centrality/betweenness→ betweenness()
  /api/network/path                  → shortest_path()
  /api/network/neighbors/{bizno}     → neighbors()
  /api/network/components            → components()

데이터 주의
  입력 그래프는 ``edge_graph.build_graph`` 가 ``public.node/edge`` 에서 만든다. 그
  테이블이 현재 비어 있어(드리프트, edge_graph.py 참조) 실호출 시 빈 그래프가 와
  결과도 비는 점에 유의 — 알고리즘 자체는 정상이며 데이터 계층만의 한계다.

복잡도 표기
  N=노드수, M=엣지수. 데모 규모(N≤1k)에선 모든 함수가 즉시 응답하나, betweenness
  는 O(N·M) 로 그래프가 커지면 가장 먼저 느려진다.

향후
  ``nice_poc.propagation`` / ``matrix`` 의 도메인 특화(Leontief / BiCGStab /
  spectral radius) 통합은 별 단계. 쇼크 전파는 본 모듈이 아니라
  ``nice_graph.shock.propagate`` 가 담당(별개 계산 경로).
"""

from __future__ import annotations

import networkx as nx

# ─── 기본 통계 ─────────────────────────────────────────────────────────────


def summary(g: nx.DiGraph) -> dict[str, object]:
    """그래프 한눈 통계 — 규모·밀도·연결성·순환 여부.

    - density        : 실제 엣지수 / 가능한 최대 엣지수(N·(N-1)). 0=고립, 1=완전그래프.
                       거래망은 보통 매우 희소(예 0.003)라 hub-and-spoke 성향.
    - weakly_*       : 방향 무시 시 덩어리 수. 1 이면 전체가 하나로 약연결.
    - strongly_*     : 방향 유지 시 상호도달 덩어리 수. 거래망은 대개 단방향이라
                       strong 컴포넌트가 노드수에 근접(=상호 거래 사이클이 드묾).
    - is_dag         : 순환(거래 사이클) 없으면 True. 공급망 상류/하류 위계의 지표.
    """
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
    """in/out degree centrality 상위 top_k (연결 많은 허브 식별).

    degree centrality = 그 노드의 차수 / (N-1) (가능한 최대 차수로 정규화, 0~1).
      in  = 들어오는 엣지(이 기업에서 매입한 거래상대 수 ≈ 공급처 다양성).
      out = 나가는 엣지(이 기업이 판매한 거래상대 수 ≈ 판로 다양성).
      total = in+out 으로 정렬 — 거래 연결이 가장 넓은 노드가 위로.
    가중치 무시(연결 '수'만). 금액 가중 영향력은 pagerank 를 볼 것. O(N).
    """
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
    """가중 PageRank 상위 top_k (거래 흐름 기준 '영향력 있는' 기업 식별).

    랜덤 워커가 엣지를 따라 이동(확률 α)하거나 임의 점프(확률 1-α)할 때, 각 노드에
    머무는 정상상태 확률. 큰 거래상대로부터 많이 '받는' 노드가 높아짐(in-flow 기준).
      alpha    : damping factor(0.85 표준). 클수록 링크 구조 의존↑, 작을수록 균등.
      weighted : True 면 edge 의 weight(예 sly_amt 공급가액)로 가중 — 금액 큰 거래에
                 비중을 둠. False 면 연결 구조만(unweighted).
    degree 와 달리 '누구와 연결됐나'(이웃의 영향력)까지 반영. 반복 수렴, 보통 빠름.
    """
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
    """Betweenness centrality 상위 top_k (병목·중개 기업 식별).

    그 노드가 '모든 쌍의 최단경로' 중 몇 %에 끼는지. 높을수록 거래 흐름의 길목(중개·
    병목) — 끊기면 공급망 우회가 길어진다. 허브(degree)와 다름: 연결은 적어도 두
    덩어리를 잇는 다리면 betweenness 가 높을 수 있음.
      weighted   : True 면 weight 를 '거리'로 보고 가중 최단경로 기준(주의: 금액이
                   클수록 거리가 멀게 해석되므로 의미 점검 필요).
      normalized : True 면 0~1 로 정규화(그래프 크기 무관 비교 가능).
    비용 O(N·M) — 본 모듈에서 가장 무거움. 그래프가 커지면 가장 먼저 느려진다.
    """
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
    """source→target 최단 경로 (방향 유지 — 거래 방향대로만 이동).

    weighted=True 면 Dijkstra(weight 를 거리로 합산 최소화), False 면 BFS(홉 수 최소).
    반환: found / path(노드열) / length(가중합 또는 홉수) / hops(=len(path)-1).
    누락·무경로는 예외 대신 ``found=False`` + reason 으로 부드럽게 반환(API 친화).
    주의: 방향 그래프라 source→target 경로가 있어도 역방향은 없을 수 있음.
    """
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
    """특정 기업의 N-depth 이웃을 홉 거리별 계층(layer)으로.

    Args:
      direction: 탐색 방향.
        'out'  : 나가는 거래만 따라감 (이 기업의 판로 하류).
        'in'   : 들어오는 거래만 (이 기업의 공급처 상류). reverse 뷰로 BFS.
        'both' : 무방향(거래 방향 무시한 연결 이웃).
      depth: 최대 홉 수(cutoff).

    구현: 방향에 맞는 그래프 뷰(복사 없음, as_view/copy=False — 메모리 절약)에서
    single-source BFS 로 각 노드까지의 홉 거리를 구하고, 거리별로 묶어 layers 로 반환.
    layers={'0':[자기], '1':[직접이웃], '2':[2홉], ...}. O(이웃 부분그래프 크기).
    """
    if bizno not in g:
        return {"found": False, "reason": f"bizno {bizno!r} not in graph"}

    # 방향별 그래프 뷰 선택 (원본 복사 없이 가벼운 view 로).
    if direction == "both":
        h = g.to_undirected(as_view=True)
    elif direction == "in":
        h = g.reverse(copy=False)  # 들어오는 엣지를 '나가는'으로 뒤집어 같은 BFS 재사용
    elif direction == "out":
        h = g
    else:
        raise ValueError(f"unknown direction {direction!r}")

    # 거리 ≤ depth 인 노드만 (cutoff 로 BFS 조기 중단).
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
    """연결 컴포넌트 통계 (방향 무시 weak / 방향 유지 strong).

    weak  : 방향을 무시하면 한 덩어리로 이어지는 노드 집합. weak_count=1 이면 전체가
            느슨하게 하나로 연결(고립 기업 없음).
    strong: 서로 '오갈 수' 있는(상호 도달) 노드 집합. 거래는 대개 단방향(셀러→바이어)
            이라 strong 컴포넌트가 잘게 쪼개짐 — strong_count 가 노드수에 가까우면
            상호 거래 사이클이 드물다는 뜻(= is_dag 에 가까움).
    각각 크기 내림차순 정렬 후 최대 컴포넌트 크기·상위 top_k 크기를 반환.
    """
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
