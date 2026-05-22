from fastapi import APIRouter
from sqlalchemy import text

from nice_poc.db import get_pg_engine, get_redis, neo4j_session

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/deep")
def health_deep() -> dict[str, str]:
    checks: dict[str, str] = {}

    try:
        with get_pg_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"fail: {exc.__class__.__name__}"

    try:
        with neo4j_session() as s:
            s.run("RETURN 1").consume()
        checks["neo4j"] = "ok"
    except Exception as exc:
        checks["neo4j"] = f"fail: {exc.__class__.__name__}"

    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"fail: {exc.__class__.__name__}"

    return checks
