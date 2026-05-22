from fastapi import FastAPI

from nice_poc import __version__
from nice_poc.api.routers import (
    aggregates,
    firms,
    health,
    kpi,
    network,
    runs,
    scenarios,
    search,
)

app = FastAPI(title="NICE PoC Backend", version=__version__)
app.include_router(health.router)
app.include_router(kpi.router)
app.include_router(scenarios.router)
app.include_router(runs.router)
app.include_router(network.router)
app.include_router(firms.router)
app.include_router(aggregates.router)
app.include_router(search.router)
