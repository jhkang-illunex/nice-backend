"""LLM 백엔드 환경변수 추상화 (OpenAI-호환).

dev: ollama (`http://llm:11434/v1`, ``LLM_MODEL=qwen2.5:7b-instruct``).
prod GPU: vLLM (동일 포트, model id = `Qwen/Qwen2.5-7B-Instruct`).
외부: OpenAI / Anthropic proxy 등 — base_url 만 변경.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    base_url: str = Field(default="http://llm:11434/v1", alias="LLM_BASE_URL")
    model: str = Field(default="qwen2.5:7b-instruct", alias="LLM_MODEL")
    api_key: str = Field(default="noop", alias="LLM_API_KEY")
    timeout_s: float = Field(default=60.0, alias="LLM_TIMEOUT_S")
    # thinking 계열 모델(qwen3 등) 추론 제어. ollama OpenAI-호환 엔드포인트는
    # 'none'|'low'|'medium'|'high' 를 받는다. 빈 문자열이면 body 에 미포함
    # (thinking 미지원 모델/백엔드에 보내면 400 나는 경우가 있어 opt-in).
    reasoning_effort: str = Field(default="", alias="LLM_REASONING_EFFORT")


@lru_cache
def get_llm_settings() -> LlmSettings:
    return LlmSettings()
