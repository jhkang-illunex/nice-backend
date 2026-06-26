"""문장형 질의 → 품목 키워드 추출 (LLM, 조건부 전처리).

"경주마를 수입할 때 어떤 HS 코드를 사용하나요?" 같은 문장형 질의는
'코드'·'수입' 등 비품목 토큰이 trigram/tsvector 시그널을 오염시킨다
(예: '코드'가 타이어 코드사 5902 류에 매칭). 문장형으로 판단될 때만
LLM 으로 품목 표현을 추출해 검색 질의로 사용한다.

안전장치 (실패해도 기존 검색보다 나빠질 수 없게)
  - 키워드형 질의는 LLM 미호출 — ``looks_like_sentence`` 게이트, 레이턴시 0 추가
  - 추출 항목은 원 질의에 실제 등장(공백 무시 부분일치)하는 것만 통과 — 환각 주입 차단
  - LLM 실패/타임아웃/빈 결과 → ``None`` 반환, 호출측은 원 질의로 폴백
  - ``RAG_QUERY_EXTRACT=false`` 로 전체 비활성화 가능
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from functools import lru_cache

from nice_llm import LlmJsonClient, get_llm_client
from nice_rag.config import get_rag_settings
from nice_rag.search.normalize import normalize_query

log = logging.getLogger(__name__)

# 문장형 신호 — 질문 어미/무역 절차 어휘. 품목명에는 거의 등장하지 않는 토큰만.
_NOISE = re.compile(
    r"(수입|수출|관세|세율|세번|부호|코드|신고|통관|얼마|어떻|어떤|무엇|"
    r"알려|적용|해당|필요|궁금|되나요|하나요|인가요|입니까|할까요|주세요|싶은데|\?)"
)


def looks_like_sentence(q_norm: str) -> bool:
    """정규화된 질의가 문장형(품목 추출이 필요한 형태)인지 휴리스틱 판정."""
    if "?" in q_norm:
        return True
    tokens = q_norm.split()
    if len(tokens) >= 6:
        return True
    return len(tokens) >= 3 and bool(_NOISE.search(q_norm))


def filter_items(items: list[str], q_norm: str) -> list[str]:
    """원 질의에 실제 등장하는 항목만 통과 — LLM 환각 주입 차단.

    비교는 공백 제거 후 부분일치 (띄어쓰기 변형 허용). 최대 3개.
    """
    flat_q = q_norm.replace(" ", "")
    out: list[str] = []
    for raw in items:
        it = normalize_query(str(raw))
        if len(it) < 2 or it in out:
            continue
        if it.replace(" ", "") in flat_q:
            out.append(it)
    return out[:3]


_SYSTEM = (
    "무역 질의에서 검색용 품목(상품) 명칭만 추출하는 도구입니다. "
    "질문에 실제로 쓰인 품목 표현을 그대로(조사만 제거) 골라내고, "
    "국가명·회사명·관세·세율·HS·코드·수입·수출 같은 비품목 단어는 제외하세요. "
    "수치는 구분합니다 — 시황·거래 조건(가격 20% 상승, 5톤 주문 등)은 제외하되, "
    "품목의 규격·성상 수치(브릭스 값, 두께, 비중, 도수 등 분류 기준)는 품목 "
    "표현에 포함해 유지하세요. WTI·LNG 같은 시세·원자재 약어는 품목으로 "
    "취급합니다. "
    "품목의 동력원·재료·구동방식을 나타내는 수식어가 그 자체로 거래 대상이 "
    "아니면 제외하고 품목 본체만 추출하세요 (예: '배터리로 구동되는 전기차'의 "
    "품목은 '전기차'). 다만 그 부품·소재 자체가 거래 품목이면 그대로 추출합니다 "
    "(예: '리튬이온 배터리셀'은 배터리가 품목 본체). "
    '오직 JSON {"items": ["..."]} 만 출력하세요.'
)

_FEWSHOT = (
    '질문: 경주마를 수입할 때 어떤 HS 코드를 사용하나요?\n→ {"items": ["경주마"]}\n'
    '질문: 미국산 반도체용 포토레지스트의 관세율이 얼마인가요\n→ {"items": ["반도체용 포토레지스트"]}\n'
    '질문: 프랑스에서 제빵용 밀가루와 버터를 수입하려고 합니다\n→ {"items": ["제빵용 밀가루", "버터"]}\n'
    '질문: WTI 가격이 20% 상승하면 어떤 기업이 영향을 받아?\n→ {"items": ["WTI"]}\n'
    '질문: 브릭스 값이 20 이하인 오렌지 주스의 HS 부호가 궁금합니다\n'
    '→ {"items": ["브릭스 값이 20 이하인 오렌지 주스"]}\n'
    '질문: 배터리로만 굴러가는 순수 전기 승용차의 세번이 궁금해요\n'
    '→ {"items": ["순수 전기 승용차"]}\n'
    '질문: 리튬이온 배터리셀 수입 신고 코드\n→ {"items": ["리튬이온 배터리셀"]}\n'
)


@lru_cache
def _extract_client() -> LlmJsonClient:
    # 검색 hot path 에 끼는 호출 — 운영 LLM 타임아웃(수십~수백 초) 대신
    # 짧은 전용 타임아웃. 초과 시 chat_json 이 빈 dict 를 돌려줘 폴백된다.
    s = get_rag_settings()
    return LlmJsonClient(
        inner=replace(get_llm_client(), timeout_s=s.query_extract_timeout_s)
    )


def _llm_items(query: str) -> list[str]:
    res = _extract_client().chat_json(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"{_FEWSHOT}질문: {query}\n→"},
        ],
        temperature=0.0,
        max_tokens=128,
    )
    items = res.get("items")
    if not isinstance(items, list):
        return []
    return [str(x) for x in items]


def extract_goods(query: str) -> str | None:
    """문장형 질의에서 품목 표현 추출. 비문장형/실패 시 None (원 질의 사용)."""
    s = get_rag_settings()
    if not s.query_extract_enabled:
        return None
    q_norm = normalize_query(query)
    if not looks_like_sentence(q_norm):
        return None
    kept = filter_items(_llm_items(query), q_norm)
    if not kept:
        log.info("query extract fallback (no valid items): %s", query)
        return None
    extracted = " ".join(kept)
    log.info("query extract: %r -> %r", query, extracted)
    return extracted
