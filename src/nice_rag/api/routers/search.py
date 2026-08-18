"""통합 검색 — HSCode + KSIC 를 병렬 호출해 단일 응답으로 반환.

엔드포인트
  GET /api/search — q 하나로 /api/hsk/search 와 /api/ksic/search 를 동시에
  실행하고 두 결과를 함께 돌려준다.

구현 노트
  - 두 도메인 검색은 각자 임베딩 HTTP 호출 + PG round-trip 을 가지므로
    순차 실행하면 레이턴시가 합산된다 — ``asyncio.gather`` + ``to_thread``
    로 병렬화해 벽시계 시간은 느린 쪽 하나로 수렴.
  - 기존 라우터 함수(hsk.search / ksic.search)를 그대로 호출한다 — 질의
    정규화·동의어 확장·CRAG(hsk 특화) 등 도메인별 파이프라인이 자동으로
    승계되고, 튜닝이 두 곳으로 갈라지지 않는다.
  - 부분 실패 허용: 한쪽 백엔드만 죽었을 때 전체 503 대신 산 쪽 결과 +
    ``errors`` 로 응답한다 (둘 다 실패면 503).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from nice_rag.api.routers import hsk as hsk_router
from nice_rag.api.routers import ksic as ksic_router
from nice_rag.api.routers.hsk import ErrorResponse, HskHit
from nice_rag.api.routers.ksic import KsicHit

router = APIRouter(prefix="/api/search", tags=["search"])
log = logging.getLogger(__name__)


# ─── 응답 스키마 ─────────────────────────────────────────────────────────────


class UnifiedSearchResponse(BaseModel):
    """HSCode + KSIC 통합 검색 결과."""

    hsk: list[HskHit] = Field(
        default_factory=list,
        description="HSCode 후보 (RRF 점수 내림차순). /api/hsk/search 와 동일 스키마.",
    )
    ksic: list[KsicHit] = Field(
        default_factory=list,
        description="KSIC 11차 대·중분류 후보. /api/ksic/search 와 동일 스키마.",
    )
    errors: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "부분 실패 시 실패한 도메인('hsk'/'ksic')과 원인. 정상이면 빈 객체. "
            "두 도메인 모두 실패하면 응답 자체가 503."
        ),
        examples=[{}],
    )


_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "두 도메인 모두 실패 (임베딩 백엔드 또는 PostgreSQL 도달 불가).",
    },
    422: {
        "model": ErrorResponse,
        "description": "요청 파라미터 검증 실패 (q 길이 등).",
    },
}


def _exc_detail(exc: BaseException) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return f"{exc.__class__.__name__}: {exc}"


# ─── 엔드포인트 ──────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=UnifiedSearchResponse,
    summary="HSCode + KSIC 통합 검색 (병렬 hybrid)",
    description=(
        "키워드 하나로 HSCode(/api/hsk/search)와 KSIC 11차 대·중분류"
        "(/api/ksic/search)를 **병렬** 실행해 두 결과를 한 응답에 담는다. "
        "각 도메인의 검색 파이프라인(hsk: 품목 추출·동의어 확장·CRAG / "
        "ksic: raw 질의)은 개별 엔드포인트와 동일. 한 도메인만 실패하면 "
        "200 + `errors` 로 부분 결과를 반환하고, 둘 다 실패하면 503."
    ),
    responses=_RESPONSES,
)
async def search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="검색 키워드 (품목/업종 자유 표현).",
        examples=["반도체"],
    ),
    limit: int = Query(
        10,
        ge=1,
        le=50,
        description="도메인별 반환 후보 수 (hsk·ksic 각각 적용).",
    ),
    hs_prefix: str | None = Query(
        None,
        pattern=r"^\d{2,8}$",
        description="[hsk 전용] HS 코드 prefix 로 검색 범위 제한 (류 2자리 ~ 세번 8자리).",
    ),
    active_only: bool = Query(
        False,
        description="[hsk 전용] true 면 현재 유효한(valid_to >= 오늘) 코드만 검색.",
    ),
    level: int | None = Query(
        None,
        ge=1,
        le=2,
        description="[ksic 전용] 계층 제한 — 1=대분류만, 2=중분류만. 생략 시 둘 다.",
    ),
) -> UnifiedSearchResponse:
    # 라우터 함수는 sync(스레드풀 실행 전제)라 to_thread 로 감싼다.
    # 인자는 전부 명시 — 기본값이 Query(...) 객체라 생략하면 오동작.
    hsk_res, ksic_res = await asyncio.gather(
        asyncio.to_thread(
            hsk_router.search,
            q=q, limit=limit, hs_prefix=hs_prefix, active_only=active_only,
        ),
        asyncio.to_thread(
            ksic_router.search,
            q=q, limit=limit, level=level,
        ),
        return_exceptions=True,
    )

    out = UnifiedSearchResponse()
    if isinstance(hsk_res, BaseException):
        log.warning("unified search: hsk failed: %s", _exc_detail(hsk_res))
        out.errors["hsk"] = _exc_detail(hsk_res)
    else:
        out.hsk = hsk_res
    if isinstance(ksic_res, BaseException):
        log.warning("unified search: ksic failed: %s", _exc_detail(ksic_res))
        out.errors["ksic"] = _exc_detail(ksic_res)
    else:
        out.ksic = ksic_res

    if len(out.errors) == 2:
        raise HTTPException(
            status_code=503,
            detail=f"all backends failed — hsk: {out.errors['hsk']} / ksic: {out.errors['ksic']}",
        )
    return out
