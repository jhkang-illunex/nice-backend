"""graph-analysis 서비스 — PostgreSQL 기반 외생충격 전파 REST API.

그래프 노드/엣지를 전부 PostgreSQL(`public.company_edge` / `public.company`)에서
read-only 로 읽어 전파한다. **Neo4j 는 사용하지 않는다.**

전파 엔진은 자체 구현(`nice_graph.shock.propagate` — round-by-round 거듭제곱급수
합 `Σ_k Rᵏ·init`)이며, 레거시 `nice_poc.propagation`(Leontief 직접해법)은 import
하지 않는다(불용·참고자료 한정). API 는 `nice_graph.shock` 위 얇은 라우팅 계층.
"""

__version__ = "0.1.0"
