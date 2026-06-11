"""통칭 → 관세율표 색인 용어 확장 사전.

무역 실무 통칭(듀럼밀, 영구자석, 전기동 등)이 KIS 계층/품목명에 없는 경우
검색이 실패한다 — 질의에 색인된 공식 용어를 덧붙여 ts/vec 시그널의 매칭을
복원한다. 값은 반드시 *실제 rag.hsk 색인 텍스트에 존재하는 표현* 이어야 한다
(예: 8505.11 의 KIS 계층명은 '영구자석'이 아니라 '전자석 > 금속으로 만든 것').

확장은 치환이 아니라 *덧붙임* — 원 질의 토큰은 보존되어 trigram(name_ko)
시그널은 영향이 적고, ts(가중 tsvector)·vec(임베딩)이 추가 용어로 보강된다.
"""

from __future__ import annotations

from nice_rag.search.normalize import normalize_query

# 통칭(정규화 후 부분일치) → 색인 용어. 검증: 2026-06-11 확장 평가의 실패 사례.
SYNONYMS: dict[str, str] = {
    # 곡물 — KIS 계층에 '듀럼' 단계 명칭이 없어 1001 호 용어로 확장
    "듀럼밀": "밀 종자",
    "듀럼 밀": "밀 종자",
    "소맥": "밀",
    "원맥": "밀 제분용",
    "대두": "콩",
    # 자석 — 8505 의 KIS 호 명칭은 '전자석' (영구자석 소호명 생략됨)
    "네오디뮴": "전자석 금속으로 만든 것",
    "영구자석": "전자석",
    "페라이트 자석": "전자석 산화철",
    # 금속/소재
    "희토류": "희토류금속 스칸듐 이트륨",
    "전기동": "정제한 구리 음극",
    # 전지 — 공식 용어는 '축전지'
    "리튬이온전지": "리튬이온 축전지",
    "리튬이온 배터리": "리튬이온 축전지",
    "배터리": "축전지",
    # 화장품 — 3304 호 명칭
    "화장품": "미용이나 메이크업용 제품류 기초화장용 제품류",
}


def expand_query(query: str) -> str:
    """정규화된 질의에 매칭되는 통칭의 색인 용어를 덧붙여 반환."""
    q_norm = normalize_query(query)
    additions: list[str] = []
    for alias, official in SYNONYMS.items():
        if alias in q_norm and official not in q_norm:
            additions.append(official)
    if not additions:
        return q_norm
    # 중복 토큰 제거하면서 순서 유지
    seen = set(q_norm.split())
    extra = [t for t in " ".join(additions).split() if t not in seen and not seen.add(t)]
    return q_norm + " " + " ".join(extra) if extra else q_norm
