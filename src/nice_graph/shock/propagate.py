"""쇼크 전파 — round-by-round propagation 의 거듭제곱급수 합.

알고리즘
  total_effect = Σ_k R^k @ init
  매 라운드 ``next_shock[target] += current_shock[source] * weight`` 를 모든
  edge 에 대해 누적. ``|propagated| > epsilon`` 만 전파 → 자연 수렴.

수렴 보증
  edges 의 ``rate`` 가 source 의 outgoing 행 정규화 (Σ_out ≤ 1) 이면 spectral
  radius ρ(R) ≤ 1 이라 절대 수렴. ``fetch_subgraph`` 의 ``all_rate`` /
  ``years_rate`` 가 정확히 이 형태이므로 짝 맞음.

안전장치
  epsilon (default 1e-8) — round 내 모든 |propagated| 가 epsilon 이하면 종료
  max_iter (default 500) — 병리적 케이스에서 무한 루프 방지. 도달 시
                           converged=False 로 반환.

input
  edges: [{'from_bizno', 'to_bizno', 'rate'}]
  init_sub_graph: {bizno: shock_value}

output (ShockResult)
  shock_list: [{'bizno', 'shock'}] — 각 노드의 누적 파급 (Σ_k 의 합)
  total_shock: float — Σ shock_list
  iterations: int — 실제 진행한 round 수
  converged: bool — epsilon 컷오프로 자연 종료했는지

권장 호출 패턴 (모듈 3 → 모듈 2)
  ``extract_first_target`` 이 반환한 N 개 1차 기업을 init_sub_graph 의 key
  로 모두 묶어 **단일 호출** 로 propagate. 시드 N 배 늘려도 wall time 은
  거의 평평 (선형성 + active set union — 실측 N=500 에서 1.6 배). N 번
  개별 호출은 동일 결과를 N 배 시간으로 계산하는 낭비.

      >>> primary = extract_first_target(node_list=[...])      # 모듈 3
      >>> sg = fetch_subgraph(hscode, n_of_child=3)            # 모듈 1
      >>> result = propagate_shock(                            # 모듈 2 — 한 번
      ...     edges=[{'from_bizno': e['from_bizno'],
      ...             'to_bizno':   e['to_bizno'],
      ...             'rate':       e['all_rate']} for e in sg.edges],
      ...     init_sub_graph={b: 1.0 for b in primary},        # 균등 default
      ... )

  *균등* (`{b: 1.0 for b in primary}`) 이 default 가정. 기업별 차등 충격이
  필요하면 호출자가 dict 값을 직접 산정해 넘김 (시그니처 무변경).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TypedDict

log = logging.getLogger(__name__)

DEFAULT_EPSILON = 1e-8
DEFAULT_MAX_ITER = 500


class EdgePropagateRow(TypedDict):
    from_bizno: str
    to_bizno: str
    rate: float


class ShockRow(TypedDict):
    bizno: str
    shock: float


class DampedCycle(TypedDict):
    members: list[str]      # 순환덩어리(SCC) 노드 id
    rho: float              # 원래 spectral radius ρ(M_S) (≥1 이라 발산)
    factor: float           # 적용한 조건부 damping (0.95^k, ρ<1 만들 때까지)
    rho_after: float        # damping 후 ρ (<1)


@dataclass
class ShockResult:
    shock_list: list[ShockRow] = field(default_factory=list)
    total_shock: float = 0.0
    iterations: int = 0
    converged: bool = True
    damped_cycles: list[DampedCycle] = field(default_factory=list)  # SCC 닫힌해 전용


def propagate_shock(
    *,
    edges: Iterable[EdgePropagateRow | Mapping[str, object]],
    init_sub_graph: Mapping[str, float],
    epsilon: float = DEFAULT_EPSILON,
    max_iter: int = DEFAULT_MAX_ITER,
) -> ShockResult:
    """edges 와 초기 충격으로 거듭제곱급수 합을 round-by-round 로 누적.

    참고 알고리즘 (사용자 제공) 의 IO 만 본 모듈 사양으로 맞춤.
    """
    # 1) edges 를 source 기준 인덱싱 (이 group-by 만 O(N), 이후 round 마다
    #    current_shock 의 source 만 순회하면 되어 hop 당 O(현재 활성 source
    #    의 fan-out) 으로 떨어짐.
    out_by_src: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in edges:
        src = str(e["from_bizno"])
        tgt = str(e["to_bizno"])
        rate = float(e["rate"])  # type: ignore[arg-type]
        out_by_src[src].append((tgt, rate))

    total_effect: dict[str, float] = defaultdict(float)
    current_shock: dict[str, float] = {b: float(v) for b, v in init_sub_graph.items()}

    # 초기 충격을 round 0 결과로 누적
    for node, value in current_shock.items():
        total_effect[node] += value

    iteration = 0
    converged = True
    while iteration < max_iter:
        next_shock: dict[str, float] = defaultdict(float)
        # 활성 source 만 순회 — current_shock 에 들어 있는 노드만
        for src, val in current_shock.items():
            for tgt, rate in out_by_src.get(src, ()):
                propagated = val * rate
                if abs(propagated) > epsilon:
                    next_shock[tgt] += propagated

        if not next_shock:
            break

        for node, value in next_shock.items():
            total_effect[node] += value

        iteration += 1
        current_shock = dict(next_shock)
    else:
        # while ... else: break 없이 max_iter 도달
        converged = False
        log.warning(
            "propagate_shock hit max_iter=%d without converging (ρ(R) may be ≥ 1)",
            max_iter,
        )

    shock_list: list[ShockRow] = [
        ShockRow(bizno=b, shock=float(v)) for b, v in total_effect.items()
    ]
    total = float(sum(total_effect.values()))

    log.info(
        "propagate_shock: nodes_in=%d, edges=%d, iterations=%d, converged=%s, total=%.6f",
        len(init_sub_graph),
        len(out_by_src),
        iteration,
        converged,
        total,
    )

    return ShockResult(
        shock_list=shock_list,
        total_shock=total,
        iterations=iteration,
        converged=converged,
    )


DEFAULT_CYCLE_DAMPING = 0.95


def propagate_shock_scc(
    *,
    edges: Iterable[EdgePropagateRow | Mapping[str, object]],
    init_sub_graph: Mapping[str, float],
    cycle_damping: float = DEFAULT_CYCLE_DAMPING,
) -> ShockResult:
    """전역 반복 없이 total = (I−M)⁻¹·init 를 SCC 분해로 정확히 계산.

    알고리즘 (= 반복 루프 안 돌리고 1회 위상순회 + 순환은 등비급수 닫힌해)
      1. 그래프를 강결합성분(SCC)으로 분해 → 응축 DAG.
      2. 위상순서(상류 SCC 먼저)로 1회 순회. 각 SCC S 의 외부 유입
         e[j] = init[j] + Σ_{i∉S, i→j} rate·x[i] (상류는 이미 확정).
      3. SCC 내부는 등비급수 닫힌해:  x_S = (I − M_S)⁻¹ · e_S
         여기서 M_S[j,i] = rate(i→j) (i,j∈S).  비순환 단일노드는 x=e (즉시).

    조건부 damping (이사님 컨펌)
      순환 SCC 의 round-trip 배율 = spectral radius ρ(M_S). ρ≥1 이면 등비급수가
      발산((I−M_S) 특이행렬)하므로, **ρ<1 이 될 때까지 M_S 에 cycle_damping(0.95)
      을 곱한다**(보통 ρ=1 인 닫힌 사이클은 1회로 0.95). 적용 내역은
      result.damped_cycles 로 표면화.  비반복이라 iterations=0, converged=True.
    """
    import networkx as nx  # noqa: PLC0415 — 무거운 의존, 이 경로에서만 로드
    import numpy as np  # noqa: PLC0415

    if not (0.0 < cycle_damping < 1.0):
        raise ValueError(f"cycle_damping 은 (0,1) 범위여야 함: {cycle_damping}")

    preds: dict[str, list[tuple[str, float]]] = defaultdict(list)
    nodes: set[str] = {str(b) for b in init_sub_graph}
    g = nx.DiGraph()
    for e in edges:
        src = str(e["from_bizno"])
        tgt = str(e["to_bizno"])
        rate = float(e["rate"])  # type: ignore[arg-type]
        preds[tgt].append((src, rate))
        g.add_edge(src, tgt)
        nodes.update((src, tgt))
    g.add_nodes_from(nodes)

    init = {str(b): float(v) for b, v in init_sub_graph.items()}
    x: dict[str, float] = dict.fromkeys(nodes, 0.0)
    damped: list[DampedCycle] = []

    cond = nx.condensation(g)
    for scc_id in nx.topological_sort(cond):       # 상류(소스) SCC 먼저
        members = list(cond.nodes[scc_id]["members"])
        idx = {n: k for k, n in enumerate(members)}
        n = len(members)
        # 외부 유입 e
        e_vec = np.zeros(n)
        for j in members:
            e_vec[idx[j]] = init.get(j, 0.0)
            for i, rate in preds[j]:
                if i not in idx:                    # 상류 SCC (이미 확정)
                    e_vec[idx[j]] += rate * x[i]
        # 내부 블록 M_S[j,i] = rate(i→j)
        m = np.zeros((n, n))
        self_loop = False
        for j in members:
            for i, rate in preds[j]:
                if i in idx:
                    m[idx[j], idx[i]] += rate
                    if i == j:
                        self_loop = True

        if n == 1 and not self_loop:               # 비순환 단일노드 — 즉시
            xs = e_vec
        else:                                       # 순환덩어리 — 등비급수 닫힌해
            rho = float(max(abs(np.linalg.eigvals(m))))
            factor = 1.0
            rho_eff = rho
            while rho_eff >= 1.0 - 1e-12:           # 조건부 damping: ρ<1 까지 ×0.95
                m = m * cycle_damping
                factor *= cycle_damping
                rho_eff *= cycle_damping
            if factor < 1.0:
                damped.append(
                    DampedCycle(
                        members=members,
                        rho=round(rho, 6),
                        factor=round(factor, 6),
                        rho_after=round(rho_eff, 6),
                    )
                )
            xs = np.linalg.solve(np.eye(n) - m, e_vec)
        for j in members:
            x[j] = float(xs[idx[j]])

    shock_list: list[ShockRow] = [
        ShockRow(bizno=b, shock=v) for b, v in x.items() if abs(v) > 0.0
    ]
    total = float(sum(v for v in x.values()))
    log.info(
        "propagate_shock_scc: nodes=%d edges=%d sccs=%d damped=%d total=%.6f",
        len(nodes), g.number_of_edges(), cond.number_of_nodes(), len(damped), total,
    )
    return ShockResult(
        shock_list=shock_list,
        total_shock=total,
        iterations=0,            # 전역 반복 없음
        converged=True,          # 조건부 damping 으로 항상 유한
        damped_cycles=damped,
    )


def propagate_dispatch(
    *,
    edges: Iterable[EdgePropagateRow | Mapping[str, object]],
    init_sub_graph: Mapping[str, float],
    method: str = "iterative",
    epsilon: float = DEFAULT_EPSILON,
    max_iter: int = DEFAULT_MAX_ITER,
    cycle_damping: float = DEFAULT_CYCLE_DAMPING,
) -> ShockResult:
    """method 로 전파 엔진 선택 — iterative(반복 거듭제곱급수) | scc(닫힌해+조건부damping).

    호출자(run_propagation·scenario)가 **propagate_kwargs 로 받은 method/엔진별 인자를
    여기서 분기·필터한다. 엔진별 인자가 섞여 와도 해당 엔진 것만 전달.
    """
    if method == "scc":
        return propagate_shock_scc(
            edges=edges, init_sub_graph=init_sub_graph, cycle_damping=cycle_damping
        )
    if method == "iterative":
        return propagate_shock(
            edges=edges, init_sub_graph=init_sub_graph, epsilon=epsilon, max_iter=max_iter
        )
    raise ValueError(f"method 는 iterative|scc: {method!r}")
