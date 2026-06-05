"""rag-server 전용 설정 — LLM / 임베딩 백엔드 URL 추상화.

원격(자체 호스팅 ollama/vLLM/TEI)이든 외부(OpenAI/Anthropic proxy)든
**OpenAI-호환 REST** 면 모두 동일 인터페이스. 백엔드 교체 = `.env` 의
``*_BASE_URL`` 1줄 수정.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RagSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── LLM (OpenAI-호환 chat completions) ────────────────────────────────────
    llm_base_url: str = Field(default="http://llm:11434/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="qwen2.5:7b-instruct", alias="LLM_MODEL")
    llm_api_key: str = Field(default="noop", alias="LLM_API_KEY")
    llm_timeout_s: float = Field(default=60.0, alias="LLM_TIMEOUT_S")

    # ── 임베딩 (OpenAI-호환 /v1/embeddings) ────────────────────────────────────
    # 기본 백엔드 = TEI CPU 컨테이너(`profile embed-local`), 기본 모델 = BAAI/bge-m3
    # (XLM-R 기반 다국어 retrieval, TEI candle backend 호환, 1024-d).
    #
    # 권장 사용 패턴은 모델 family 별로 다름:
    #   - BGE-M3 (현 default)   : query 도 raw text — instruct prefix 권장 X
    #   - Qwen3-Embedding 류    : query 에 "Instruct: ...\nQuery: ..." prefix 권장
    # `embed_query_instruction` 이 비어 있으면 prefix 미적용(raw) — BGE-M3 표준.
    # Qwen3 로 swap 시엔 EMBED_QUERY_INSTRUCTION 채우면 자동 prefix.
    #
    # 코사인 유사도 색인(`vector_cosine_ops`) 사용 → 임베딩 정규화 권장(`embed_normalize=True`).
    embed_base_url: str = Field(default="http://embed:8080/v1", alias="EMBED_BASE_URL")
    embed_model: str = Field(default="BAAI/bge-m3", alias="EMBED_MODEL")
    embed_api_key: str = Field(default="noop", alias="EMBED_API_KEY")
    embed_dim: int = Field(default=1024, alias="EMBED_DIM")
    embed_timeout_s: float = Field(default=30.0, alias="EMBED_TIMEOUT_S")
    embed_normalize: bool = Field(default=True, alias="EMBED_NORMALIZE")
    embed_query_instruction: str = Field(
        default="",  # BGE-M3 표준: raw query. Qwen3 swap 시 .env 에서 채움.
        alias="EMBED_QUERY_INSTRUCTION",
    )


@lru_cache
def get_rag_settings() -> RagSettings:
    return RagSettings()
