"""triple_list(엣지 목록) 입력의 순수 쇼크 시나리오 — DB 의존 없음.

입력 그래프를 클라이언트가 제공한다:
  triple_list : [{"from": p1, "to": p2, "rate": w}, ...]  거래쌍·거래비율(trade_rate).
  seed_list   : [{"node_id": p1, "shock_amount": v}, ...]  1차 기업과 노드별
                주입 충격금액(**원 단위**, 음수=감소).
  directions  : [0|1] 목록. 0=downstream(셀러→바이어, 매출 파급),
                              1=upstream(바이어→셀러, 매입 파급).
                triple_list 는 저장방향(from=셀러→to=바이어) 기준이고, direction=1 이면
                전파 시 엣지를 뒤집어 바이어→셀러로 흐른다.

시나리오 — 두 시나리오는 shock_amount 의 **해석**만 다르고 계산은 동일하다.
  tariff : 시드별 shock_amount(외생 충격금액, 원) 주입 → 전파 → 결과=파급 금액.
  volume : 시드별 shock_amount(거래량 변동금액, 원, 0=무변화) 주입 → 전파 →
           결과=변동금액. 조정액 = 기준액 + 변동금액. tariff 와 0-기준(0=무변화) 통일.

시드 필터링: triple_list 의 노드 집합(from∪to)에 없는 시드는 전파에서 제외하고
DirectionResult["excluded"] 로 보고한다 — init·depth·total_shock 어디에도 포함되지 않는다.
(stateless API 라 시드/그래프를 클라이언트가 따로 조립하므로, node_id 불일치를 조용히
결과에 남기는 대신 명시적으로 돌려준다.)

pin_seeds(기본 False): 시드 incoming 엣지를 끊지 않고 순환 되먹임 포함(일반균형). 기본 동작이며
임펄스(True, 시드 고정)는 사용하지 않음 — 결과는 항상 일반균형. (NICE 확정 2026-06-25)
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
    excluded: list[str]     # triple_list 노드 집합에 없어 전파에서 제외된 시드


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


def _norm_seeds(seed_list: Sequence[Mapping]) -> dict[str, float]:
    """[{node_id, shock_amount}] → {node_id: amount}. 중복 node_id 는 합산."""
    out: dict[str, float] = {}
    for s in seed_list:
        nid = s.get("node_id")
        amt = s.get("shock_amount")
        if nid is None or amt is None:
            raise ValueError(f"seed 형식 오류 (node_id/shock_amount 필요): {dict(s)}")
        out[str(nid)] = out.get(str(nid), 0.0) + float(amt)
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


def _run_seeded(
    triple_list: Sequence[Mapping],
    seed_list: Sequence[Mapping],
    directions: Sequence[int],
    *,
    pin_seeds: bool,
    method: Method,
    cycle_damping: float,
) -> list[DirectionResult]:
    """공통 러너 — 시드별 shock_amount 주입 후 요청 방향별 전파.

    triple_list 의 노드 집합(from∪to)에 없는 시드는 init·depth 에서 제외하고
    excluded 로 보고한다. 노드 집합은 방향과 무관(뒤집기만 하므로)해서 한 번만 계산.
    """
    triples = _norm_triples(triple_list)
    amounts = _norm_seeds(seed_list)
    graph_nodes = {f for f, _, _ in triples} | {t for _, t, _ in triples}
    excluded = sorted(nid for nid in amounts if nid not in graph_nodes)
    init = {nid: v for nid, v in amounts.items() if nid in graph_nodes}
    seeds = list(init)
    seed_set = set(seeds)
    out: list[DirectionResult] = []
    for d in directions:
        edges = _oriented_edges(triples, int(d))
        res = _propagate_one(
            edges, dict(init), seed_set,
            pin_seeds=pin_seeds, method=method, cycle_damping=cycle_damping,
        )
        out.append(
            DirectionResult(
                direction=int(d), result=res,
                depths=_bfs_depth(edges, seeds), excluded=list(excluded),
            )
        )
    return out


def run_tariff(
    triple_list: Sequence[Mapping],
    seed_list: Sequence[Mapping],
    directions: Sequence[int],
    *,
    pin_seeds: bool = False,
    method: Method = "scc",
    cycle_damping: float = 0.95,
) -> list[DirectionResult]:
    """관세 충격 — 시드별 shock_amount(외생 충격금액, 원, 음수 가능) 주입 후 방향별 전파.

    seed_list: [{"node_id": p1, "shock_amount": v}, ...]. 전역 shock_rate(비율) 는 폐기 —
    충격금액을 시드마다 개별 지정한다. 결과 shock_list 도 금액(원).
    """
    return _run_seeded(
        triple_list, seed_list, directions,
        pin_seeds=pin_seeds, method=method, cycle_damping=cycle_damping,
    )


def run_volume(
    triple_list: Sequence[Mapping],
    seed_list: Sequence[Mapping],
    directions: Sequence[int],
    *,
    pin_seeds: bool = False,
    method: Method = "scc",
    cycle_damping: float = 0.95,
) -> list[DirectionResult]:
    """거래량 변동 — 시드별 shock_amount(변동금액, 원) 주입 → 전파 → 결과=변동금액.

    seed_list: [{"node_id": p1, "shock_amount": v}, ...]. v = 거래량 변동금액
    (원, 0=무변화, 음수=감소). 결과 shock_list 의 각 값도 **변동금액**(0=무변화).
    조정액 = 기준액 + 변동금액. (과거 node_overrides[{p1,delta}] 입력 폐기 —
    tariff 와 동일하게 seed_list 로 통일. 계산은 tariff 와 동일, 해석만 다름.)
    """
    return _run_seeded(
        triple_list, seed_list, directions,
        pin_seeds=pin_seeds, method=method, cycle_damping=cycle_damping,
    )
