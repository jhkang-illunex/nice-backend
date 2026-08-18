"""문장형 질의 → 업종(산업활동) 표현 추출 (LLM, 조건부 전처리) — KSIC 검색용.

``extract.py``(HS 품목 추출)와 구조 동일, **프롬프트와 문장형 게이트만 업종
도메인 전용**이다. "반도체 만드는 회사는 어떤 산업분류에 속하나요?" 류의
질의에서 '회사'·'분류'·'속하나요' 같은 비업종 토큰이 임베딩을 희석하고
trigram/tsvector 를 오염시키는 것을 막는다.

안전장치는 extract.py 와 동일 원칙 — 실패해도 기존 검색보다 나빠질 수 없다:
  - 키워드형 질의는 LLM 미호출 (``looks_like_industry_sentence`` 게이트)
  - 추출 항목은 원 질의에 실제 등장하는 것만 통과 (``filter_items`` 재사용)
  - LLM 실패/타임아웃/빈 결과 → ``None`` 반환, 호출측은 원 질의로 폴백
  - ``RAG_KSIC_EXTRACT=false`` 로 전체 비활성화 가능
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from functools import lru_cache

from nice_llm import LlmJsonClient, get_llm_client
from nice_rag.config import get_rag_settings
from nice_rag.search.extract import filter_items
from nice_rag.search.normalize import normalize_query

log = logging.getLogger(__name__)

# 문장형 신호 — 질문 어미 + 업종/등록 절차 어휘. 업종 표현 자체에는 거의
# 등장하지 않는 토큰만 (예: '제조'·'서비스'는 업종명 구성어라 제외).
_NOISE = re.compile(
    r"(산업분류|업종|분류|코드|등록|사업자|회사|기업|스타트업|창업|운영|"
    r"속하|해당|무엇|어떤|어떻|알려|궁금|되나요|하나요|인가요|입니까|"
    r"할까요|주세요|싶은데|\?)"
)


def looks_like_industry_sentence(q_norm: str) -> bool:
    """정규화된 질의가 문장형(업종 추출이 필요한 형태)인지 휴리스틱 판정."""
    if "?" in q_norm:
        return True
    tokens = q_norm.split()
    if len(tokens) >= 6:
        return True
    return len(tokens) >= 3 and bool(_NOISE.search(q_norm))


_SYSTEM = (
    "질의에서 산업분류 검색용 업종(사업 활동) 표현만 추출하는 도구입니다. "
    "질문에 실제로 쓰인 표현을 그대로(조사·어미만 제거) 골라내고, "
    "회사·기업·사업자·업종·분류·코드·등록 같은 비업종 단어와 지역명·회사명은 "
    "제외하세요. "
    "무엇을 만들거나(제조), 팔거나(도소매), 제공하는지(서비스)를 나타내는 "
    "대상·활동 표현이 추출 대상입니다 — 활동 동사가 어미 변형이면 명사형 "
    "어간만 남기되, 원문에 없는 단어를 새로 만들지 마세요 "
    "(예: '만드는 회사' 에서 '제조' 를 지어내지 말고 만드는 대상만 추출). "
    "여러 활동이 나오면 각각 별도 항목으로 나눕니다. "
    "'파는'·'만드는' 같은 동사 활용형만 단독으로 추출하지 말고, 그 대상 "
    "명사와 업태 명사(쇼핑몰·도매 등)를 추출하세요. "
    '오직 JSON {"items": ["..."]} 만 출력하세요.'
)

_FEWSHOT = (
    '질의: 반도체 만드는 회사는 어떤 산업분류에 속하나요?\n→ {"items": ["반도체"]}\n'
    '질의: 음식점을 운영하는 개인사업자의 업종코드가 궁금합니다\n→ {"items": ["음식점"]}\n'
    '질의: 화물 운송 사업을 하려면 무슨 업종으로 등록해야 하나요\n→ {"items": ["화물 운송"]}\n'
    '질의: 소프트웨어 개발과 데이터베이스 구축을 하는 스타트업입니다\n'
    '→ {"items": ["소프트웨어 개발", "데이터베이스 구축"]}\n'
    '질의: 서울에서 커피 원두를 볶아 도매로 납품하는 회사\n→ {"items": ["커피 원두", "도매"]}\n'
    '질의: 우리 회사는 자동차 부품을 제조합니다\n→ {"items": ["자동차 부품", "제조"]}\n'
    '질의: 온라인으로 옷을 파는 쇼핑몰을 운영하고 있어요\n→ {"items": ["옷", "쇼핑몰"]}\n'
)


@lru_cache
def _extract_client() -> LlmJsonClient:
    # 검색 hot path — extract.py 와 동일하게 짧은 전용 타임아웃 (설정 공유).
    s = get_rag_settings()
    return LlmJsonClient(
        inner=replace(get_llm_client(), timeout_s=s.query_extract_timeout_s)
    )


def _llm_items(query: str) -> list[str]:
    res = _extract_client().chat_json(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"{_FEWSHOT}질의: {query}\n→"},
        ],
        temperature=0.0,
        max_tokens=128,
    )
    items = res.get("items")
    if not isinstance(items, list):
        return []
    return [str(x) for x in items]


def extract_industry(query: str) -> str | None:
    """문장형 질의에서 업종 표현 추출. 비문장형/실패 시 None (원 질의 사용)."""
    s = get_rag_settings()
    if not s.ksic_extract_enabled:
        return None
    q_norm = normalize_query(query)
    if not looks_like_industry_sentence(q_norm):
        return None
    # min_len=1 — '옷'·'꽃'·'쌀' 같은 한글 1자 대상이 유효한 업종 표현.
    kept = filter_items(_llm_items(query), q_norm, min_len=1)
    if not kept:
        log.info("ksic extract fallback (no valid items): %s", query)
        return None
    extracted = " ".join(kept)
    log.info("ksic extract: %r -> %r", query, extracted)
    return extracted
