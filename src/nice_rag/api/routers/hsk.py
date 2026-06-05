"""HSCode 검색 / 자연어 질의 에이전트.

엔드포인트
  GET /api/hsk/search   — 키워드 → 임베딩 + RRF hybrid → 후보 리스트
  GET /api/hsk/agent    — 자연어 질의 → 검색 → LLM 한국어 요약/근거 인용

LLM / 임베딩 백엔드는 ``LLM_BASE_URL`` / ``EMBED_BASE_URL`` 만 바라본다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from nice_rag.clients import get_llm_client
from nice_rag.config import get_rag_settings
from nice_rag.search.hsk_embed import embed_query
from nice_rag.search.hsk_index import HybridHit, search_hybrid

router = APIRouter(prefix="/api/hsk", tags=["hsk"])
log = logging.getLogger(__name__)


# ─── 응답 스키마 ─────────────────────────────────────────────────────────────


class HskHit(BaseModel):
    """검색 후보 1건."""

    hs_code: str = Field(
        ...,
        description="관세청 HS 부호 10자리 (앞 0 포함).",
        examples=["0101211000"],
    )
    name_ko: str | None = Field(
        None,
        description="한글 품목명. 원본 데이터 없을 시 null.",
        examples=["농가 사육용"],
    )
    name_en: str | None = Field(
        None,
        description="영문 품목명.",
        examples=["For farm breeding"],
    )
    description: str | None = Field(
        None,
        description=(
            "검색용으로 결합된 텍스트(name_ko | standard_trade_name | "
            "nature_integrated_name | name_en | hs_content). 빈 슬롯은 ' |  | ' 형태."
        ),
        examples=["농가 사육용 |  | (말) | For farm breeding | "],
    )
    score: float = Field(
        ...,
        description=(
            "RRF 결합 점수. 3 시그널(임베딩/trigram/tsvector) 모두 rank=1 일 때 "
            "이론적 최대 ≈ 0.0492 (= 3/(60+1))."
        ),
        examples=[0.0492],
    )


class HskAnswer(BaseModel):
    """자연어 답변 + 근거 인용."""

    answer: str = Field(
        ...,
        description=(
            "LLM 이 생성한 한국어 답변. 후보가 없을 때는 "
            "'해당 질의에 매칭되는 HS 부호 후보를 찾지 못했습니다.' 고정 메시지."
        ),
        examples=["경주말은 HS 0101291000 을 사용합니다."],
    )
    citations: list[HskHit] = Field(
        default_factory=list,
        description="LLM 컨텍스트로 제공된 RRF 검색 결과 (k 개). 환각 검증용 ground truth.",
    )


class ErrorResponse(BaseModel):
    """RFC 7807 의 단순화 형태 — FastAPI 의 기본 detail 필드만."""

    detail: str = Field(
        ...,
        description="에러 메시지. 503 은 의존성 도달성 실패, 422 는 요청 검증 실패.",
        examples=["embed backend unreachable (http://embed:8080/v1): ConnectError"],
    )


# ─── 내부 헬퍼 ───────────────────────────────────────────────────────────────


_AGENT_SYSTEM_PROMPT = (
    "당신은 한국 관세청 HS 부호 전문가입니다. "
    "다음은 사용자 질의에 대해 trigram + 임베딩 hybrid 검색으로 추려진 후보 HS 부호 리스트입니다. "
    "후보 내에서만 근거를 인용해 한국어로 간결히 답변하세요. "
    "확신이 없으면 '확실하지 않음'이라고 답하세요. 추측 금지."
)


def _to_hit(h: HybridHit) -> HskHit:
    return HskHit(
        hs_code=h.hs_code,
        name_ko=h.name_ko,
        name_en=h.name_en,
        description=h.description,
        score=h.score,
    )


def _format_context(hits: list[HybridHit]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(
            f"[{i}] {h.hs_code} | {h.name_ko or '-'} | {h.name_en or '-'} | "
            f"{(h.description or '-')[:200]}"
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


def _search_or_503(query: str, qvec: list[float], limit: int) -> list[HybridHit]:
    try:
        return search_hybrid(query_text=query, query_vec=qvec, limit=limit)
    except SQLAlchemyError as exc:
        log.exception("hsk search failed")
        raise HTTPException(
            status_code=503,
            detail=(
                f"hsk search failed — table not migrated or DB unreachable: "
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
    response_model=list[HskHit],
    summary="HSCode 키워드/의미 검색 (RRF hybrid)",
    description=(
        "한국어 또는 영문 키워드를 받아 임베딩 + trigram + tsvector "
        "3 시그널의 Reciprocal Rank Fusion 으로 결합 검색. "
        "정확 매칭 시 score ≈ 0.0492 (이론적 최대)."
    ),
    responses=_SEARCH_RESPONSES,
)
def search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=200,
        description="검색 키워드 (한국어/영문 자유).",
        examples=["농가 사육용 말"],
    ),
    limit: int = Query(
        10,
        ge=1,
        le=50,
        description="반환할 후보 개수. 운영 권장 5~10.",
    ),
) -> list[HskHit]:
    qvec = _embed_or_503(q)
    hits = _search_or_503(q, qvec, limit)
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
    response_model=HskAnswer,
    summary="자연어 질의 → 검색 → LLM 한국어 답변",
    description=(
        "자연어 질문을 받아 hybrid 검색으로 후보 k 건을 추리고, LLM 이 후보 "
        "내에서만 인용해 한국어로 답변. citations 가 항상 ground truth — "
        "LLM 답변이 부실해도 citations[0] 이 정답일 확률 높음."
    ),
    responses=_AGENT_RESPONSES,
)
def agent(
    q: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="자연어 질의 (한국어 권장).",
        examples=["경주마를 수입할 때 어떤 HS 코드를 사용하나요?"],
    ),
    k: int = Query(
        5,
        ge=1,
        le=20,
        description="LLM 에 컨텍스트로 제공할 검색 후보 수. 4~8 권장.",
    ),
) -> HskAnswer:
    s = get_rag_settings()
    qvec = _embed_or_503(q)
    hits = _search_or_503(q, qvec, k)
    citations = [_to_hit(h) for h in hits]

    if not hits:
        return HskAnswer(
            answer="해당 질의에 매칭되는 HS 부호 후보를 찾지 못했습니다.",
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

    return HskAnswer(answer=answer, citations=citations)
