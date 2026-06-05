"""OpenAI-호환 chat completions 클라이언트.

자체 ollama(`http://llm:11434/v1`) / vLLM / 외부 OpenAI(`https://api.openai.com/v1`)
등 base_url 만 바뀌면 그대로 동작.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from nice_rag.config import get_rag_settings


@dataclass(frozen=True)
class LlmClient:
    base_url: str
    model: str
    api_key: str
    timeout_s: float

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """chat completions 호출 → assistant 메시지의 content 반환."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if extra:
            body.update(extra)

        with httpx.Client(timeout=self.timeout_s) as cx:
            r = cx.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"]["content"]


@lru_cache
def get_llm_client() -> LlmClient:
    s = get_rag_settings()
    return LlmClient(
        base_url=s.llm_base_url,
        model=s.llm_model,
        api_key=s.llm_api_key,
        timeout_s=s.llm_timeout_s,
    )
