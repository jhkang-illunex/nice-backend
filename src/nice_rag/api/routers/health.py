"""rag-server 의 헬스 — PG + LLM/Embed 백엔드 도달성."""

from __future__ import annotations

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text

from nice_common.db import get_pg_engine
from nice_rag.config import get_rag_settings

router = APIRouter(tags=["health"])


class LivenessResponse(BaseModel):
    """K8s liveness probe 호환 — 프로세스가 살아있는지만 확인."""

    status: str = Field(..., description="항상 'ok'.", examples=["ok"])


class DeepHealthResponse(BaseModel):
    """의존성 3종(postgres/llm/embed) 도달성 점검 결과."""

    postgres: str = Field(
        ...,
        description="원격 PostgreSQL 도달 + SELECT 1 성공 여부.",
        examples=["ok"],
    )
    llm: str = Field(
        ...,
        description=(
            "LLM 백엔드 도달 여부. `{base_url}/models` 호출 — 200/404 모두 도달 OK. "
            "모델 호출은 안 함(health 비용 낮춤)."
        ),
        examples=["ok"],
    )
    embed: str = Field(
        ...,
        description="임베딩 백엔드 도달 여부. 같은 패턴.",
        examples=["ok"],
    )


@router.get(
    "/health",
    response_model=LivenessResponse,
    summary="라이브니스",
    description="프로세스가 응답 가능한지 확인. 외부 의존성 점검 없음.",
)
def health() -> LivenessResponse:
    return LivenessResponse(status="ok")


@router.get(
    "/health/deep",
    response_model=DeepHealthResponse,
    summary="의존성 도달성",
    description=(
        "PG/LLM/Embed 세 가지 의존성을 한 번에 점검. 각 필드는 'ok' "
        "또는 'fail: {ExceptionClass}'. 운영 디버깅용 — 503 으로 인한 사용자 "
        "측 에러가 어느 의존성에서 발생했는지 즉시 분간 가능."
    ),
)
def health_deep() -> DeepHealthResponse:
    checks: dict[str, str] = {}
    s = get_rag_settings()

    try:
        with get_pg_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"fail: {exc.__class__.__name__}"

    # LLM/Embed 는 단순 도달성만(모델 호출은 안 함 — health 비용 낮춤)
    for label, base_url in (("llm", s.llm_base_url), ("embed", s.embed_base_url)):
        try:
            with httpx.Client(timeout=2.0) as cx:
                cx.get(f"{base_url.rstrip('/')}/models")  # 200 / 404 모두 도달 OK
            checks[label] = "ok"
        except Exception as exc:
            checks[label] = f"fail: {exc.__class__.__name__}"

    return DeepHealthResponse(**checks)
