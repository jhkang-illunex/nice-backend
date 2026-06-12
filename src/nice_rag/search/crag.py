"""경량 CRAG(Corrective RAG) — 검색 결과 1회 평가·보정.

/agent 경로에서 검색 결과를 LLM 이 한 번 평가한다:
  fit       : 질의 품목과 부합하는 후보 존재 → 그대로 답변 생성
  ambiguous : 질의는 무역/품목 관련인데 후보가 빗나감 → 평가기가 제시한
              대체 키워드로 1회 재검색 후 병합 (예: 'WTI' → '원유')
  unrelated : 요리법·날씨·일상 상담 등 무역과 전혀 무관 → 즉시 거부
              (답변 LLM 호출 생략 — negative 질의의 응답도 빨라짐)

설계 원칙 (fail-safe)
  - 평가기 실패/타임아웃/형식 오류 → 'fit' 으로 폴백 (현행 동작과 동일)
  - 보정은 1회로 제한 — 재검색 결과가 더 나빠도 병합이라 원 후보는 보존됨
  - 점수 임계 가드를 쓰지 않는 이유: RRF 1~2시그널 구간(0.0164~0.0328)에
    정답과 잡음이 공존해 (120문항 실측: 임계 0.033 시 정답 40건 오차단)
    점수만으로는 분리 불가 — 의미 판단은 LLM 평가기가 담당한다.

기존 자기보완 루프와의 관계: hsk_synonym_learn(오프라인 배치)이 알려진 갭을
사전에 적재한다면, CRAG 는 사전에 없는 미지의 갭을 요청 시점에 보정한다.
보정 검색도 search_log 에 남아 self-play 학습의 재료가 된다.
"""

from __future__ import annotations

import logging

from nice_llm import get_llm_json_client
from nice_rag.search.hsk_index import HybridHit

log = logging.getLogger(__name__)

VERDICT_FIT = "fit"
VERDICT_AMBIGUOUS = "ambiguous"
VERDICT_UNRELATED = "unrelated"
_VERDICTS = {VERDICT_FIT, VERDICT_AMBIGUOUS, VERDICT_UNRELATED}

_SYSTEM = (
    "당신은 HS 부호 검색 결과 평가기입니다. 사용자 질의와 검색 후보를 보고 "
    '오직 JSON {"verdict": "fit" | "ambiguous" | "unrelated", "keywords": "..."} 만 출력하세요.\n'
    "- fit: 질의에 등장하는 품목과 부합하는 후보가 있음. 관세·수출입·공급망·"
    "산업 영향 분석 질문도 품목이 등장하고 후보가 그 품목이면 fit.\n"
    "- ambiguous: 질의는 무역/품목 관련인데 후보가 그 품목과 빗나감. "
    "keywords 에 검색용 대체 한국어 품목 용어(2~4단어)를 제시.\n"
    "- unrelated: 요리법, 날씨, 제품 추천, 사용법, 일상 상담 등 수출입 품목 "
    "조회와 전혀 무관한 질의.\n"
    "예시: '철강 제품에 추가 관세 부과 시 영향은?' + 철강 후보 → fit / "
    "'WTI 가격 상승 영향은?' + 과즙 음료 후보 → ambiguous, keywords '원유' / "
    "'김치볶음밥 레시피 알려줘' → unrelated (김치 후보가 있어도)."
)


def _format_hits(hits: list[HybridHit]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h.hs_code} | {h.name_ko or '-'} | {(h.description or '-')[:80]}")
    return "\n".join(lines)


def evaluate(query: str, hits: list[HybridHit]) -> tuple[str, str]:
    """검색 결과 평가 → (verdict, 대체 keywords). 실패 시 ('fit', '') 폴백."""
    res = get_llm_json_client().chat_json(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"질의: {query}\n\n후보:\n{_format_hits(hits)}"},
        ],
        temperature=0.0,
        max_tokens=128,
    )
    verdict = str(res.get("verdict", "")).strip().lower()
    keywords = str(res.get("keywords", "")).strip()
    if verdict not in _VERDICTS:
        return (VERDICT_FIT, "")
    if verdict == VERDICT_AMBIGUOUS and not keywords:
        return (VERDICT_FIT, "")
    log.info("crag verdict=%s keywords=%r query=%r", verdict, keywords, query)
    return (verdict, keywords)


def merge_hits(original: list[HybridHit], corrected: list[HybridHit], k: int) -> list[HybridHit]:
    """원 후보 + 보정 후보 병합 — hs_code 중복은 높은 점수 유지, 점수순 상위 k."""
    best: dict[str, HybridHit] = {}
    for h in list(original) + list(corrected):
        cur = best.get(h.hs_code)
        if cur is None or h.score > cur.score:
            best[h.hs_code] = h
    return sorted(best.values(), key=lambda h: h.score, reverse=True)[:k]
