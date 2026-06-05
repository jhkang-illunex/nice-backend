"""rag-server FastAPI 진입점.

graph-analysis 와 분리된 독립 앱 — Neo4j 의존 없음, PG/Redis + LLM/Embed
원격 백엔드만 사용.
"""

from __future__ import annotations

from fastapi import FastAPI

from nice_rag import __version__
from nice_rag.api.routers import health

app = FastAPI(title="NICE rag-server", version=__version__)
app.include_router(health.router)
# /api/hsk 라우터는 검색/임베딩 백엔드 wiring 후 활성 — 다음 commit 에서 include.
