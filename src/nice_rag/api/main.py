"""rag-server FastAPI 진입점.

graph-analysis 와 분리된 독립 앱 — Neo4j 의존 없음, PG + LLM/Embed
원격 백엔드만 사용.
"""

from __future__ import annotations

from fastapi import FastAPI

from nice_rag import __version__
from nice_rag.api.routers import health, hsk

_DESCRIPTION = """
**NICE PoC rag-server** — 관세청 HSCode 검색 + 자연어 질의 에이전트.

* **/api/hsk/search** — 키워드/의미 hybrid 검색 (임베딩 + trigram + tsvector RRF)
* **/api/hsk/agent**  — 자연어 질의 → 검색 → LLM 한국어 답변

### 백엔드 추상화
LLM / 임베딩 백엔드는 OpenAI-호환 base_url 만 바라봅니다. 자체 호스팅
(ollama / vLLM / TEI) ↔ 외부 API (OpenAI / Anthropic proxy) 전환은
`.env` 의 `LLM_BASE_URL` / `EMBED_BASE_URL` 1줄 변경.

### 데이터
PostgreSQL `rag.hsk` 테이블에 12,469 row + 1024-d 임베딩(BAAI/bge-m3)
적재. NICE 운영 인스턴스의 기존 31 public 테이블과 schema 격리.

### 명세서
[`docs/RAG_API.md`](https://github.com/jhkang-illunex/nice-backend/blob/main/docs/RAG_API.md)
"""


app = FastAPI(
    title="NICE rag-server",
    version=__version__,
    description=_DESCRIPTION,
    contact={
        "name": "NICE PoC team (illunex)",
        "email": "jhkang@illunex.com",
    },
    license_info={"name": "Proprietary"},
    openapi_tags=[
        {
            "name": "health",
            "description": "라이브니스 + 의존성 도달성 점검 (postgres/llm/embed).",
        },
        {
            "name": "hsk",
            "description": "관세청 HSCode 검색 + 자연어 에이전트 (RRF hybrid + LLM).",
        },
    ],
)
app.include_router(health.router)
app.include_router(hsk.router)
