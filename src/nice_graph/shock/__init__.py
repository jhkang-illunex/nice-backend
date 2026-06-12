"""쇼크 파급 모듈 — 그래프 조회 + 전파 + 1차 대상 선정.

  fetch_subgraph(hscode, n_of_child, mode)
      HS → 시드 (s_em001 ⋈ s_ra603) → BFS/DFS n 차 확장 → nodes + edges
      edges 의 ``all_rate`` = source bizno 의 outgoing 행 정규화 (Σ=1)
      edges 의 ``years_rate[yr]`` = source bizno 의 연도별 outgoing 중 비중

  propagate_shock(edges, init_sub_graph)
      [MOCK — 다음 세션에서 수렴 루프 구현]
      초기 충격을 그대로 반환.

  extract_first_target(node_list)
      LLM (nice_llm.LlmJsonClient.classify_choice) 으로 HIGH+MEDIUM 분류.
"""

from nice_graph.shock.fetch import (
    EdgeRow,
    NodeRow,
    SubgraphResult,
    fetch_subgraph,
)
from nice_graph.shock.propagate import ShockResult, propagate_shock
from nice_graph.shock.target import extract_first_target

__all__ = [
    "EdgeRow",
    "NodeRow",
    "ShockResult",
    "SubgraphResult",
    "extract_first_target",
    "fetch_subgraph",
    "propagate_shock",
]
