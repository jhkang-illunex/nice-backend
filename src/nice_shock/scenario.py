"""triple_list(엣지 목록) 입력의 순수 쇼크 시나리오 — DB 의존 없음.

입력 그래프를 클라이언트가 제공한다:
  triple_list : [{"from": p1, "to": p2, "rate": w}, ...]  거래쌍·거래비율(trade_rate).
  seed_list   : [p1, p2, ...]  외생충격을 받는 1차 기업.
  directions  : [0|1] 목록. 0=downstream(셀러→바이어, 매출 파급),
                              1=upstream(바이어→셀러, 매입 파급).
                triple_list 는 저장방향(from=셀러→to=바이어) 기준이고, direction=1 이면
                전파 시 엣지를 뒤집어 바이어→셀러로 흐른다.

시나리오
  tariff : 시드에 shock_rate 외생 주입 → 전파.
  volume : node_overrides 의 노드에 delta(0-기준 변동율) 주입 → 전파 → 결과=변동율
           (δ=0 노드는 정확히 0=무변화). 조정액 = 기준액×(1+변동율). tariff 와 0-기준 통일.

pin_seeds(기본 True): 시드로 들어오는 엣지를 끊어 시드를 주입값에 고정(자기 순환 증폭 차단).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict

from nice_shock.engine import ShockResult, propagate_dispatch

# direction 코드 ↔ 의미
DOWNSTREAM = 0  # 셀러→바이어 (매출 파급)
UPSTREAM = 1    # 바이어→셀러 (매입 파급)
Method = Literal["scc", "iterative"]


class Triple(TypedDict):
    from_: str
    to: str
    rate: float


class DirectionResult(TypedDict):
    direction: int
    result: ShockResult
    depths: dict[str, int]  # 노드별 depth (시드=1, 시드에서 홉당 +1)


def _norm_triples(triple_list: Sequence[Mapping]) -> list[tuple[str, str, float]]:
    """다양한 키 표기({from/from_/p1}, {to/p2}, {rate/w/w1})를 (from,to,rate) 로 정규화."""
    out: list[tuple[str, str, float]] = []
    for t in triple_list:
        f = t.get("from") or t.get("from_") or t.get("p1")
        to = t.get("to") or t.get("p2")
        r = t.get("rate", t.get("w", t.get("w1")))
        if f is None or to is None or r is None:
            raise ValueError(f"triple 형식 오류 (from/to/rate 필요): {dict(t)}")
        out.append((str(f), str(to), float(r)))
    return out


def _oriented_edges(triples: list[tuple[str, str, float]], direction: int) -> list[dict]:
    """direction 에 맞춰 엣지 방향 결정. upstream(1)이면 뒤집는다."""
    edges = []
    for f, to, r in triples:
        src, dst = (to, f) if direction == UPSTREAM else (f, to)
        edges.append({"from_bizno": src, "to_bizno": dst, "rate": r})
    return edges


def _bfs_depth(edges: list[dict], seeds: Sequence[str]) -> dict[str, int]:
    """시드에서 전파 방향(edges)으로 BFS 한 노드별 depth. 시드=1, 홉당 +1."""
    from collections import deque

    adj: dict[str, list[str]] = {}
    for e in edges:
        adj.setdefault(e["from_bizno"], []).append(e["to_bizno"])
    depth = {str(s): 1 for s in seeds}
    q = deque((str(s), 1) for s in seeds)
    while q:
        node, d = q.popleft()
        for nb in adj.get(node, ()):
            if nb not in depth:
                depth[nb] = d + 1
                q.append((nb, d + 1))
    return depth


def _propagate_one(
    edges: list[dict],
    init: dict[str, float],
    seed_set: set[str],
    *,
    pin_seeds: bool,
    method: Method,
    cycle_damping: float,
) -> ShockResult:
    if pin_seeds:  # 시드 incoming 차단 → 시드 주입값 고정
        edges = [e for e in edges if e["to_bizno"] not in seed_set]
    return propagate_dispatch(
        edges=edges, init_sub_graph=init, method=method, cycle_damping=cycle_damping
    )


def run_tariff(
    triple_list: Sequence[Mapping],
    seed_list: Sequence[str],
    shock_rate: float,
    directions: Sequence[int],
    *,
    pin_seeds: bool = True,
    method: Method = "scc",
    cycle_damping: float = 0.95,
) -> list[DirectionResult]:
    """관세 충격 — 시드에 shock_rate 외생 주입 후 요청 방향별 전파."""
    triples = _norm_triples(triple_list)
    seeds = [str(s) for s in seed_list]
    seed_set = set(seeds)
    init = {s: float(shock_rate) for s in seeds}
    out: list[DirectionResult] = []
    for d in directions:
        edges = _oriented_edges(triples, int(d))
        res = _propagate_one(
            edges, init, seed_set, pin_seeds=pin_seeds, method=method, cycle_damping=cycle_damping
        )
        out.append(
            DirectionResult(direction=int(d), result=res, depths=_bfs_depth(edges, seeds))
        )
    return out


def run_volume(
    triple_list: Sequence[Mapping],
    seed_list: Sequence[str],
    edge_overrides: Sequence[Mapping],
    directions: Sequence[int],
    *,
    pin_seeds: bool = True,
    method: Method = "scc",
    cycle_damping: float = 0.95,
) -> list[DirectionResult]:
    """거래량 변동 — node_overrides 의 delta(0-기준 변동율) 를 그 노드에 주입 → 전파.

    node_overrides: [{"p1": node, "delta": d}, ...]. d = 변동율(0=무변화, +0.1=+10%, 음수=감소).
                    tariff 의 shock_rate 와 동일한 0-기준 편차를 그 노드 init 에 그대로 주입.
    결과 shock_list 의 각 값은 **변동율**(0=무변화). 조정액 = 기준액 × (1+변동율).
    (입·출력 모두 0-기준 — tariff 와 통일. 과거 1-기준 factor(w1)·shock=1+δ 폐기.)
    """
    triples = _norm_triples(triple_list)
    seeds = [str(s) for s in seed_list]
    seed_set = set(seeds)
    # δ(변동율) 주입 노드 — 0-기준 그대로
    delta: dict[str, float] = {}
    for ov in edge_overrides:
        node = ov.get("p1") or ov.get("node") or ov.get("bizno")
        dv = ov.get("delta", ov.get("d"))
        if node is None or dv is None:
            raise ValueError(f"node_override 형식 오류 (p1/delta 필요): {dict(ov)}")
        delta[str(node)] = delta.get(str(node), 0.0) + float(dv)
    out: list[DirectionResult] = []
    for d in directions:
        edges = _oriented_edges(triples, int(d))
        res = _propagate_one(
            edges, dict(delta), seed_set, pin_seeds=pin_seeds, method=method, cycle_damping=cycle_damping
        )
        # 출력 = 전파된 변동율 그대로 (0-기준, δ=0 노드 = 0=무변화)
        out.append(
            DirectionResult(direction=int(d), result=res, depths=_bfs_depth(edges, seeds))
        )
    return out
