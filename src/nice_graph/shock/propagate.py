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


@dataclass
class ShockResult:
    shock_list: list[ShockRow] = field(default_factory=list)
    total_shock: float = 0.0
    iterations: int = 0
    converged: bool = True


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
