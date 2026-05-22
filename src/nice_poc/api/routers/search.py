"""검색: pg_trgm autocomplete + pgvector semantic. 인덱스 적재 후 활성화."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from nice_poc.api.schemas import AutocompleteHit, SemanticHit

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("/autocomplete", response_model=list[AutocompleteHit])
def autocomplete(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(10, ge=1, le=50),
) -> list[AutocompleteHit]:
    raise HTTPException(status_code=501, detail="data not loaded yet")


@router.get("/semantic", response_model=list[SemanticHit])
def semantic(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=50),
) -> list[SemanticHit]:
    raise HTTPException(status_code=501, detail="data not loaded yet")
