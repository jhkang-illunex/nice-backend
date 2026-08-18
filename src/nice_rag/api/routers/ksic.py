"""KSIC(한국표준산업분류 제11차) 검색 / 자연어 질의 에이전트.

엔드포인트
  GET /api/ksic/search — 키워드 → 임베딩 + RRF hybrid → 대·중분류 후보 리스트
  GET /api/ksic/agent  — 자연어 질의 → 검색 → LLM 한국어 답변

조회 범위는 제11차 대분류(A~U 21개)·중분류(2자리 77개)까지 — 소분류 이하는
row 로 노출하지 않되 항목명이 검색 텍스트에 흡수돼 리콜을 담당한다
('반도체' → 중분류 26). hsk 라우터의 품목 추출(extract)·동의어 확장·CRAG 는
HS 품목 도메인 특화 튜닝이라 여기서는 적용하지 않는다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from nice_rag.api.routers.hsk import ErrorResponse
from nice_rag.clients import get_llm_client
from nice_rag.config import get_rag_settings
from nice_rag.search.hsk_embed import embed_query
from nice_rag.search.ksic_index import KsicHit as IndexHit
from nice_rag.search.ksic_index import search_hybrid

router = APIRouter(prefix="/api/ksic", tags=["ksic"])
log = logging.getLogger(__name__)


# ─── 응답 스키마 ─────────────────────────────────────────────────────────────


class KsicHit(BaseModel):
    """검색 후보 1건."""

    code: str = Field(
        ...,
        description="KSIC 코드 — 대분류는 영문 1자(A~U), 중분류는 2자리 숫자.",
        examples=["26"],
    )
    level: int = Field(
        ...,
        description="계층 — 1=대분류, 2=중분류.",
        examples=[2],
    )
    parent_code: str | None = Field(
        None,
        description="중분류의 소속 대분류 코드. 대분류 row 는 null.",
        examples=["C"],
    )
    name_ko: str = Field(
        ...,
        description="분류 항목명 (제11차 고시 기준).",
        examples=["전자 부품, 컴퓨터, 영상, 음향 및 통신장비 제조업"],
    )
    division_range: str | None = Field(
        None,
        description="대분류만: 포괄하는 중분류 코드 범위 (예: '10~34'). 중분류는 null.",
        examples=[None],
    )
    score: float = Field(
        ...,
        description=(
            "RRF 결합 점수. 3 시그널(임베딩/trigram/tsvector) 모두 rank=1 일 때 "
            "이론적 최대 ≈ 0.0492 (= 3/(60+1))."
        ),
        examples=[0.0492],
    )


class KsicAnswer(BaseModel):
    """자연어 답변 + 근거 인용."""

    answer: str = Field(
        ...,
        description=(
            "LLM 이 생성한 한국어 답변. 후보가 없을 때는 "
            "'해당 질의에 매칭되는 산업분류 후보를 찾지 못했습니다.' 고정 메시지."
        ),
        examples=["반도체 제조는 중분류 26 (전자 부품, 컴퓨터, 영상, 음향 및 통신장비 제조업) 에 속합니다."],
    )
    citations: list[KsicHit] = Field(
        default_factory=list,
        description="LLM 컨텍스트로 제공된 RRF 검색 결과 (k 개). 환각 검증용 ground truth.",
    )


# ─── 내부 헬퍼 ───────────────────────────────────────────────────────────────


_AGENT_SYSTEM_PROMPT = (
    "당신은 한국표준산업분류(KSIC 제11차) 전문가입니다. "
    "다음은 사용자 질의에 대해 trigram + 임베딩 hybrid 검색으로 추려진 "
    "산업분류(대분류·중분류) 후보 리스트입니다. "
    "반드시 후보 리스트에 있는 코드를 그대로 인용해, "
    "한 줄에 '코드 (계층) — 분류명' 형식으로 관련도 순으로 나열하세요. "
    "후보에 없는 코드나 분류명을 새로 만들지 마세요(분류명 바꿔쓰기 금지). "
    "이 시스템은 중분류(2자리)까지만 제공합니다 — 소분류 이하 코드를 "
    "생성하지 마세요. "
    "질의 업종에 적합한 후보가 없으면 '확실하지 않음'이라고 답하세요. 추측 금지."
)


def _to_hit(h: IndexHit) -> KsicHit:
    return KsicHit(
        code=h.code,
        level=h.level,
        parent_code=h.parent_code,
        name_ko=h.name_ko,
        division_range=h.division_range,
        score=h.score,
    )


def _format_context(hits: list[IndexHit]) -> str:
    label = {1: "대분류", 2: "중분류"}
    lines = []
    for i, h in enumerate(hits, 1):
        children = (h.children_text or "")[:300]
        lines.append(
            f"[{i}] {h.code} ({label.get(h.level, '?')}) | {h.name_ko} | "
            f"하위: {children or '-'}"
        )
    return "\n".join(lines)


def _embed_or_503(q: str) -> list[float]:
    s = get_rag_settings()
    try:
        return embed_query(q)
    except Exception as exc:
        log.exception("embed backend unreachable")
        raise HTTPException(
            status_code=503,
            detail=f"embed backend unreachable ({s.embed_base_url}): {exc.__class__.__name__}",
        ) from exc


def _search_or_503(
    query: str,
    qvec: list[float],
    limit: int,
    *,
    level: int | None = None,
) -> list[IndexHit]:
    try:
        return search_hybrid(
            query_text=query,
            query_vec=qvec,
            limit=limit,
            level=level,
        )
    except SQLAlchemyError as exc:
        log.exception("ksic search failed")
        raise HTTPException(
            status_code=503,
            detail=(
                f"ksic search failed — table not migrated or DB unreachable: "
                f"{exc.__class__.__name__}"
            ),
        ) from exc


# ─── 엔드포인트 ──────────────────────────────────────────────────────────────


_SEARCH_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "임베딩 백엔드 또는 PostgreSQL 도달 불가.",
    },
    422: {
        "model": ErrorResponse,
        "description": "요청 파라미터 검증 실패 (q 길이 등).",
    },
}


@router.get(
    "/search",
    response_model=list[KsicHit],
    summary="KSIC 11차 대·중분류 키워드/의미 검색 (RRF hybrid)",
    description=(
        "업종 키워드를 받아 임베딩 + trigram + tsvector 3 시그널의 "
        "Reciprocal Rank Fusion 으로 결합 검색. 결과는 제11차 대분류(A~U)·"
        "중분류(2자리)까지 — 소분류 이하 항목명은 검색 텍스트에 흡수돼 "
        "리콜에만 기여한다. 정확 매칭 시 score ≈ 0.0492 (이론적 최대)."
    ),
    responses=_SEARCH_RESPONSES,
)
def search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="검색 키워드 (업종명/업태 자유 표현).",
        examples=["반도체 제조"],
    ),
    limit: int = Query(
        10,
        ge=1,
        le=50,
        description="반환할 후보 개수. 운영 권장 5~10.",
    ),
    level: int | None = Query(
        None,
        ge=1,
        le=2,
        description="계층 제한 — 1=대분류만, 2=중분류만. 생략 시 둘 다.",
    ),
) -> list[KsicHit]:
    qvec = _embed_or_503(q)
    hits = _search_or_503(q, qvec, limit, level=level)
    return [_to_hit(h) for h in hits]


_AGENT_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "임베딩/LLM 백엔드 또는 PostgreSQL 도달 불가.",
    },
    422: {
        "model": ErrorResponse,
        "description": "요청 파라미터 검증 실패.",
    },
}


@router.get(
    "/agent",
    response_model=KsicAnswer,
    summary="자연어 질의 → 검색 → LLM 한국어 답변 (KSIC 11차)",
    description=(
        "자연어 질문을 받아 hybrid 검색으로 대·중분류 후보 k 건을 추리고, "
        "LLM 이 후보 내에서만 인용해 한국어로 답변. citations 가 항상 "
        "ground truth — LLM 답변이 부실해도 citations[0] 이 정답일 확률 높음."
    ),
    responses=_AGENT_RESPONSES,
)
def agent(
    q: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="자연어 질의 (한국어 권장).",
        examples=["반도체 만드는 회사는 어떤 산업분류에 속하나요?"],
    ),
    k: int = Query(
        5,
        ge=1,
        le=20,
        description="LLM 에 컨텍스트로 제공할 검색 후보 수. 4~8 권장.",
    ),
    level: int | None = Query(
        None,
        ge=1,
        le=2,
        description="계층 제한 — 1=대분류만, 2=중분류만. 생략 시 둘 다.",
    ),
) -> KsicAnswer:
    s = get_rag_settings()
    qvec = _embed_or_503(q)
    hits = _search_or_503(q, qvec, k, level=level)
    citations = [_to_hit(h) for h in hits]

    if not hits:
        return KsicAnswer(
            answer="해당 질의에 매칭되는 산업분류 후보를 찾지 못했습니다.",
            citations=citations,
        )

    user_msg = f"질의: {q}\n\n후보:\n{_format_context(hits)}"
    try:
        answer = get_llm_client().chat(
            messages=[
                {"role": "system", "content": _AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=512,
        )
    except Exception as exc:
        log.exception("llm backend unreachable")
        raise HTTPException(
            status_code=503,
            detail=f"llm backend unreachable ({s.llm_base_url}): {exc.__class__.__name__}",
        ) from exc

    return KsicAnswer(answer=answer, citations=citations)
