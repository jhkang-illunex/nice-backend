"""rag-server 의 헬스 — PG + Redis + LLM/Embed 백엔드 도달성."""

from __future__ import annotations

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from nice_poc.db import get_pg_engine, get_redis
from nice_rag.config import get_rag_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/deep")
def health_deep() -> dict[str, str]:
    checks: dict[str, str] = {}
    s = get_rag_settings()

    try:
        with get_pg_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"fail: {exc.__class__.__name__}"

    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"fail: {exc.__class__.__name__}"

    # LLM/Embed 는 단순 도달성만(모델 호출은 안 함 — health 비용 낮춤)
    for label, base_url in (("llm", s.llm_base_url), ("embed", s.embed_base_url)):
        try:
            with httpx.Client(timeout=2.0) as cx:
                cx.get(f"{base_url.rstrip('/')}/models")  # 200 / 404 모두 도달 OK
            checks[label] = "ok"
        except Exception as exc:
            checks[label] = f"fail: {exc.__class__.__name__}"

    return checks
