"""nice_llm — OpenAI-호환 chat completions 공용 클라이언트.

``nice_rag`` / ``nice_poc`` / ``nice_graph`` / ``nice_demo`` 모두 ollama / vLLM /
OpenAI / Anthropic-proxy 등 동일한 OpenAI-호환 백엔드를 호출하므로 한 곳에서
관리. TEI(임베딩) 는 RAG-only 라 ``nice_rag`` 에 남김.

Public API
  LlmClient            — raw chat completions
  LlmJsonClient        — JSON object 강제 호출 + 파싱 + 카테고리 분류 helper
  get_llm_client       — LlmClient 싱글톤 (env 기반)
  get_llm_json_client  — LlmJsonClient 싱글톤
"""

from nice_llm.client import (
    LlmClient,
    LlmJsonClient,
    get_llm_client,
    get_llm_json_client,
    parse_json_lenient,
)
from nice_llm.settings import LlmSettings, get_llm_settings

__all__ = [
    "LlmClient",
    "LlmJsonClient",
    "LlmSettings",
    "get_llm_client",
    "get_llm_json_client",
    "get_llm_settings",
    "parse_json_lenient",
]
