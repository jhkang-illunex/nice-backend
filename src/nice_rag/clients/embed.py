"""OpenAI-호환 ``/v1/embeddings`` 클라이언트.

TEI(HuggingFace Text Embeddings Inference) / vLLM 임베딩 / OpenAI 모두
같은 스펙. base_url 만 바뀌면 동작.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import httpx

from nice_rag.config import get_rag_settings


@dataclass(frozen=True)
class EmbedClient:
    base_url: str
    model: str
    api_key: str
    timeout_s: float
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        """입력 텍스트 리스트 → 임베딩 벡터 리스트(입력 순서 유지)."""
        if not texts:
            return []
        with httpx.Client(timeout=self.timeout_s) as cx:
            r = cx.post(
                f"{self.base_url.rstrip('/')}/embeddings",
                json={"model": self.model, "input": texts},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            r.raise_for_status()
            data = r.json()
        # OpenAI 스펙: {"data": [{"embedding": [...], "index": i}, ...]}
        items = sorted(data["data"], key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]


@lru_cache
def get_embed_client() -> EmbedClient:
    s = get_rag_settings()
    return EmbedClient(
        base_url=s.embed_base_url,
        model=s.embed_model,
        api_key=s.embed_api_key,
        timeout_s=s.embed_timeout_s,
        dim=s.embed_dim,
    )
