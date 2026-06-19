"""데모 전용 설정 — 외부 REST URL + LLM 백엔드.

PG 연결은 ``nice_poc.db.get_pg_engine`` 을 그대로 재사용하므로 여기에
중복 정의하지 않는다.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DemoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 같은 compose network 의 다른 서비스 호출.
    # 로컬 dev (`streamlit run`) 에서는 .env 에 http://localhost:18001/2 같이 둘 수도 있음.
    rag_api_url: str = Field(default="http://rag-server:8000", alias="RAG_API_URL")
    graph_api_url: str = Field(
        default="http://graph-analysis:8000", alias="GRAPH_API_URL"
    )
    rest_timeout_s: float = Field(default=30.0, alias="DEMO_REST_TIMEOUT_S")

    # LLM — nice_rag 와 동일한 변수명을 그대로 읽어와 백엔드 swap 일관성 유지.
    llm_base_url: str = Field(default="http://llm:11434/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="qwen2.5:7b-instruct", alias="LLM_MODEL")
    llm_api_key: str = Field(default="noop", alias="LLM_API_KEY")
    llm_timeout_s: float = Field(default=60.0, alias="LLM_TIMEOUT_S")


@lru_cache
def get_demo_settings() -> DemoSettings:
    return DemoSettings()
