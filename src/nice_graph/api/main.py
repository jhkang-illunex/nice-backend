"""graph-analysis FastAPI 진입점.

기존 ``nice_poc.api.main`` 의 라우터 중 그래프/시뮬레이션 관련만 묶고
검색(`/api/search`) 라우터는 rag-server 로 이관한다.
"""

from __future__ import annotations

from fastapi import FastAPI

from nice_graph import __version__
from nice_poc.api.routers import (
    aggregates,
    firms,
    health,
    kpi,
    network,
    runs,
    scenarios,
)

app = FastAPI(title="NICE graph-analysis", version=__version__)
app.include_router(health.router)
app.include_router(kpi.router)
app.include_router(scenarios.router)
app.include_router(runs.router)
app.include_router(network.router)
app.include_router(firms.router)
app.include_router(aggregates.router)
