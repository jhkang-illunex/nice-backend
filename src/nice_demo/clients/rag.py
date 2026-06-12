"""rag-server REST 클라이언트 — HS 검색/에이전트 thin wrapper.

대응 엔드포인트
  GET /api/hsk/search  → HsHit 리스트
  GET /api/hsk/agent   → HskAnswer (answer + citations)
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from nice_demo.config import get_demo_settings


@dataclass(frozen=True)
class RagClient:
    base_url: str
    timeout_s: float

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """RRF hybrid 검색 — hs_code/name_ko/name_en/description/score 리스트."""
        with httpx.Client(timeout=self.timeout_s) as cx:
            r = cx.get(
                f"{self.base_url.rstrip('/')}/api/hsk/search",
                params={"q": query, "limit": limit},
            )
            r.raise_for_status()
            return r.json()

    def agent(self, query: str, *, k: int = 5) -> dict[str, Any]:
        """자연어 질의 + LLM 답변 + citations (HSCode 후보)."""
        with httpx.Client(timeout=self.timeout_s) as cx:
            r = cx.get(
                f"{self.base_url.rstrip('/')}/api/hsk/agent",
                params={"q": query, "k": k},
            )
            r.raise_for_status()
            return r.json()


@lru_cache
def get_rag_client() -> RagClient:
    s = get_demo_settings()
    return RagClient(base_url=s.rag_api_url, timeout_s=s.rest_timeout_s)
