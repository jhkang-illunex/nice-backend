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

    # ── 검색 자기보완 루프 ──────────────────────────────────────────────────
    # RRF 산식: 3시그널 만점 ≈ 0.0492(=3/61), 2시그널 1위 ≈ 0.0328(=2/61),
    # 단일 시그널 1위 ≈ 0.0164(=1/61). 실측상 0.033 미만은 한 시그널만 매칭된
    # 불안정 구간 (예: '에어프라이어'→'에어해머' 0.0325 같은 trigram 오매칭).
    lowconf_threshold: float = Field(default=0.033, alias="RAG_LOWCONF_THRESHOLD")
    # self-play 검증: 확장(원질의+후보) 검색의 top1 최소 점수. 결합 질의는
    # trigram 이 희석되어 만점이 어려우므로 2시그널 정렬(0.0328) 근방으로 설정.
    # 의미 코사인·수렴 가드가 함께 적용되므로 점수 단독 기준은 완화해도 안전.
    syn_verify_threshold: float = Field(default=0.030, alias="RAG_SYN_VERIFY_THRESHOLD")

    # ── 문장형 질의 품목 추출 (LLM 전처리) ─────────────────────────────────
    # 문장형 질의의 비품목 토큰('코드'·'수입' 등) 오염을 LLM 추출로 제거.
    # 키워드형 질의는 LLM 미호출. 실패/타임아웃 시 원 질의로 폴백.
    query_extract_enabled: bool = Field(default=True, alias="RAG_QUERY_EXTRACT")
    # CPU 추론(dev)은 prompt eval 만 수~수십 초 — 환경별로 조정.
    query_extract_timeout_s: float = Field(default=15.0, alias="RAG_EXTRACT_TIMEOUT_S")


@lru_cache
def get_rag_settings() -> RagSettings:
    return RagSettings()
