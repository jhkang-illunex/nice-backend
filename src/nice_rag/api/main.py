"""rag-server FastAPI 진입점.

graph-analysis 와 분리된 독립 앱 — Neo4j 의존 없음, PG/Redis + LLM/Embed
원격 백엔드만 사용.
"""

from __future__ import annotations

from fastapi import FastAPI

from nice_rag import __version__
from nice_rag.api.routers import health, hsk

app = FastAPI(title="NICE rag-server", version=__version__)
app.include_router(health.router)
app.include_router(hsk.router)
