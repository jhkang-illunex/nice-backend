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
from nice_rag.search.crag import VERDICT_AMBIGUOUS, VERDICT_UNRELATED, evaluate, merge_hits
from nice_rag.search.extract import extract_goods
from nice_rag.search.hsk_embed import embed_query
from nice_rag.search.hsk_index import HybridHit, search_hybrid
from nice_rag.search.searchlog import log_search
from nice_rag.search.synonyms import expand_query

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
            "검색용으로 결합된 텍스트(name_ko | name_en | detail_ko | detail_en | "
            "heading_ko | standard_trade_name | nature_integrated_name | hs_content). "
            "괄호는 공백 치환됨. 빈 슬롯은 ' | | ' 형태."
        ),
        examples=["농가 사육용 | For farm breeding | 살아 있는 말ㆍ당나귀ㆍ노새ㆍ버새 > 번식용 > 농가 사육용 | Live horses > ... | | 말 | "],
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
    "반드시 후보 리스트에 있는 10자리 HS 부호를 그대로 인용해, "
    "한 줄에 'HS부호 — 품목명' 형식으로 관련도 순으로 나열하세요. "
    "후보에 없는 부호나 품목명을 새로 만들지 마세요(품목명 바꿔쓰기 금지). "
    "용어: '세번'과 '부호'는 HS 부호(코드)를 뜻합니다. "
    "관세율·세율 수치는 이 시스템이 제공하지 않습니다 — 질의가 세율이나 수치를 "
    "묻더라도 거부하지 말고 해당 품목에 적합한 HS 부호 후보를 나열하세요. "
    "공급망·산업 영향 질문도 품목이 등장하면 그 품목의 후보를 나열하세요. "
    "질의 품목에 적합한 후보가 없으면 '확실하지 않음'이라고 답하세요. 추측 금지."
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


def _crag_correct(
    q: str,
    hits: list[HybridHit],
    k: int,
    *,
    hs_prefix: str | None,
    active_only: bool,
) -> tuple[str, str, list[HybridHit]]:
    """CRAG 평가 + ambiguous 시 1회 보정 재검색·병합.

    Returns (verdict, keywords, hits). 평가기 실패는 evaluate() 가 'fit' 으로
    폴백하므로 호출측 동작은 기존과 동일 — 켜서 나빠질 수 없다.
    """
    verdict, keywords = evaluate(q, hits)
    if verdict == VERDICT_AMBIGUOUS:
        kw_norm = expand_query(keywords) or keywords
        try:
            hits2 = _search_or_503(
                kw_norm, _embed_or_503(kw_norm), k,
                hs_prefix=hs_prefix, active_only=active_only,
            )
        except HTTPException:
            hits2 = []  # 보정 실패 — 원 후보로 진행
        if hits2:
            log_search(q, f"[crag] {kw_norm}", hits2)
            hits = merge_hits(hits, hits2, k)
    return verdict, keywords, hits


def _search_or_503(
    query: str,
    qvec: list[float],
    limit: int,
    *,
    hs_prefix: str | None = None,
    active_only: bool = False,
) -> list[HybridHit]:
    try:
        return search_hybrid(
            query_text=query,
            query_vec=qvec,
            limit=limit,
            hs_prefix=hs_prefix,
            active_only=active_only,
        )
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
        "정확 매칭 시 score ≈ 0.0492 (이론적 최대). "
        "top1 이 저신뢰(< 0.033)면 CRAG 평가기가 1회 보정 — 품목 조회와 "
        "무관한 질의로 판정되면 빈 리스트(추천 불가)를 반환."
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
    hs_prefix: str | None = Query(
        None,
        pattern=r"^\d{2,8}$",
        description="HS 코드 prefix 로 검색 범위 제한 (류 2자리 ~ 세번 8자리). 예: 85 (전기기기), 8703 (승용차).",
        examples=["85"],
    ),
    active_only: bool = Query(
        False,
        description="true 면 현재 유효한(valid_to >= 오늘) 코드만 검색.",
    ),
) -> list[HskHit]:
    # 문장형이면 품목만 추출(실패 시 원 질의) → 정규화 + 동의어 확장.
    # 동의어 매칭은 추출 전 원문에서도 검사 — 추출이 통칭을 잘라도 확장 발동.
    s = get_rag_settings()
    q_search = extract_goods(q) or q
    q_norm = expand_query(q_search, match_text=q) or q_search
    qvec = _embed_or_503(q_norm)
    hits = _search_or_503(q_norm, qvec, limit, hs_prefix=hs_prefix, active_only=active_only)
    log_search(q, q_norm, hits)

    # CRAG (조건부): 저신뢰(top1 < crag_search_threshold)일 때만 평가기 발동 —
    # 자신 있는 검색은 레이턴시 그대로, 의심 구간만 LLM 비용. unrelated 는 빈
    # 리스트(추천 불가). 임계는 lowconf_threshold 와 분리 — 2시그널 경계 정답
    # (LNG 0.0328)이 거부되지 않도록 1시그널 구간만 평가 대상으로 둔다(config 참조).
    if s.crag_search_enabled and hits and hits[0].score < s.crag_search_threshold:
        verdict, _, hits = _crag_correct(
            q, hits, limit, hs_prefix=hs_prefix, active_only=active_only
        )
        if verdict == VERDICT_UNRELATED:
            return []
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
    hs_prefix: str | None = Query(
        None,
        pattern=r"^\d{2,8}$",
        description="HS 코드 prefix 로 검색 범위 제한 (류 2자리 ~ 세번 8자리).",
    ),
    active_only: bool = Query(
        False,
        description="true 면 현재 유효한(valid_to >= 오늘) 코드만 검색.",
    ),
) -> HskAnswer:
    s = get_rag_settings()
    q_search = extract_goods(q) or q
    q_norm = expand_query(q_search, match_text=q) or q_search
    qvec = _embed_or_503(q_norm)
    hits = _search_or_503(q_norm, qvec, k, hs_prefix=hs_prefix, active_only=active_only)
    log_search(q, q_norm, hits)
    citations = [_to_hit(h) for h in hits]

    if not hits:
        return HskAnswer(
            answer="해당 질의에 매칭되는 HS 부호 후보를 찾지 못했습니다.",
            citations=citations,
        )

    # CRAG 1회 보정 — unrelated 즉시 거부(답변 LLM 생략), ambiguous 재검색·병합.
    # 평가기 실패 시 'fit' 폴백이라 켜서 나빠질 수 없음. crag.py 참조.
    crag_note = ""
    if s.crag_enabled:
        verdict, keywords, corrected = _crag_correct(
            q, hits, k, hs_prefix=hs_prefix, active_only=active_only
        )
        if verdict == VERDICT_UNRELATED:
            return HskAnswer(answer="확실하지 않음 (품목 조회와 무관)", citations=citations)
        if corrected is not hits:
            hits = corrected
            citations = [_to_hit(h) for h in hits]
            # 답변 LLM 이 보정 사실을 모르면 "질의 품목이 후보에 없다"고
            # 정직 거부한다 (예: 'WTI' vs 후보 '역청유') — 해석을 명시.
            crag_note = f"\n(질의의 품목은 '{keywords}' 로 해석되어 후보가 보정되었습니다)"

    user_msg = f"질의: {q}{crag_note}\n\n후보:\n{_format_context(hits)}"
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
