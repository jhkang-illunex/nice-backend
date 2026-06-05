"""graph-analysis FastAPI 진입점.

본 PoC 단계 = **네트워크 분석 데모 뼈대**. 운영 PG (.env 의 POSTGRES_*) 의
``public.node`` / ``public.edge`` 를 read-only SELECT 로 읽어 networkx 로
계산한 결과를 JSON 으로 반환.

기존 ``nice_poc.api.routers`` (scenarios/runs/network/firms/aggregates/kpi)
는 Neo4j + nice_poc.propagation 의존이라 별 단계에서 통합 — 본 데모엔 미포함.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from sqlalchemy import text

from nice_graph import __version__
from nice_graph.api.routers import network
from nice_poc.db import get_pg_engine

_DESCRIPTION = """
**NICE PoC graph-analysis** — `public.node` / `public.edge` 기반 네트워크 분석 데모.

* **/api/network/summary** — 그래프 기본 통계
* **/api/network/centrality/{pagerank,degree,betweenness}** — 중심성 3 종
* **/api/network/path** — 두 노드 간 최단 경로 (가중 Dijkstra)
* **/api/network/components** — 연결 컴포넌트 통계
* **/api/network/neighbors/{bizno}** — N-depth BFS 이웃

운영 PG (`172.30.1.101`) 의 31 운영 테이블은 무수정 (read-only SELECT 만).
"""


# ─── inline health 라우터 — nice_poc.api.routers.health 는 Neo4j 의존이라 사용 안 함 ──

_health = APIRouter(tags=["health"])


@_health.get("/health", summary="라이브니스")
def liveness() -> dict[str, str]:
    return {"status": "ok"}


@_health.get("/health/deep", summary="PG 도달성")
def deep() -> dict[str, str]:
    try:
        with get_pg_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"postgres": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"postgres": f"fail: {exc.__class__.__name__}"}


app = FastAPI(
    title="NICE graph-analysis",
    version=__version__,
    description=_DESCRIPTION,
    contact={
        "name": "NICE PoC team (illunex)",
        "email": "jhkang@illunex.com",
    },
    license_info={"name": "Proprietary"},
    openapi_tags=[
        {"name": "health", "description": "라이브니스 + PG 도달성."},
        {"name": "network", "description": "node/edge → networkx 분석 (데모)."},
    ],
)
app.include_router(_health)
app.include_router(network.router)
