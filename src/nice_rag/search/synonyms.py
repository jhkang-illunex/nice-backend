"""통칭 → 관세율표 색인 용어 확장 사전.

무역 실무 통칭(듀럼밀, 영구자석, 전기동 등)이 KIS 계층/품목명에 없는 경우
검색이 실패한다 — 질의에 색인된 공식 용어를 덧붙여 ts/vec 시그널의 매칭을
복원한다. 값은 반드시 *실제 rag.hsk 색인 텍스트에 존재하는 표현* 이어야 한다
(예: 8505.11 의 KIS 계층명은 '영구자석'이 아니라 '전자석 > 금속으로 만든 것').

확장은 치환이 아니라 *덧붙임* — 원 질의 토큰은 보존되어 trigram(name_ko)
시그널은 영향이 적고, ts(가중 tsvector)·vec(임베딩)이 추가 용어로 보강된다.

사전은 2계층: 코드 내 빌트인(_BUILTIN, 검증된 시드) + ``rag.synonyms`` 테이블
(hsk_synonym_learn 배치가 self-play 검증 후 자동 등록). DB 항목이 빌트인을
덮어쓰며, DB 불가 시 빌트인만으로 동작한다 (폐쇄망 안전).
"""

from __future__ import annotations

import logging
import time

from nice_rag.search.normalize import normalize_query

log = logging.getLogger(__name__)

# 통칭(정규화 후 부분일치) → 색인 용어. 검증: 2026-06-11 확장 평가의 실패 사례.
_BUILTIN: dict[str, str] = {
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
    # 자동차 — 색인은 '승용자동차'(simple 토크나이저라 '승용차'와 토큰 불일치).
    # BEV(8703.80)의 변별 토큰만: '추진용'(전기 추진 차량 공통) + '전기자동차'
    # (hs_content). '갖춘/것' 같은 일반 토큰을 넣으면 OR-ts 가 잡음을 키운다.
    "승용차": "승용자동차",
    "BEV": "추진용 전기자동차",
    "bev": "추진용 전기자동차",
    # 화장품 — 3304 호 명칭
    "화장품": "미용이나 메이크업용 제품류 기초화장용 제품류",
}


# DB 사전 캐시 — 검색 hot path 에서 매번 SELECT 하지 않도록 TTL 캐싱
_CACHE_TTL_S = 60.0
_cache: dict[str, str] | None = None
_cache_at: float = 0.0


def _load_db_synonyms() -> dict[str, str]:
    try:
        from sqlalchemy import text

        from nice_poc.db import get_pg_engine

        with get_pg_engine().connect() as c:
            rows = c.execute(
                text("SELECT alias, expansion FROM rag.synonyms WHERE enabled")
            ).fetchall()
        return {r[0]: r[1] for r in rows}
    except Exception:  # noqa: BLE001 — DB 불가 시 빌트인만으로 동작
        log.warning("rag.synonyms load failed — builtin only", exc_info=True)
        return {}


def get_synonyms() -> dict[str, str]:
    """빌트인 + DB 동의어 병합 (DB 우선, TTL 캐시)."""
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is None or now - _cache_at > _CACHE_TTL_S:
        _cache = {**_BUILTIN, **_load_db_synonyms()}
        _cache_at = now
    return _cache


def expand_query(query: str, *, match_text: str | None = None) -> str:
    """정규화된 질의에 매칭되는 통칭의 색인 용어를 덧붙여 반환.

    ``match_text`` 가 주어지면 동의어 *매칭 검사*는 그 텍스트에서도 수행한다
    (확장 용어가 덧붙는 대상은 여전히 ``query``). LLM 품목 추출이 통칭의
    일부를 잘라내도('기초화장품 스킨 로션'→'스킨 로션') 추출 전 원문에 있던
    통칭('화장품')의 확장이 발동하도록 하기 위함.
    """
    q_norm = normalize_query(query)
    scan = q_norm if match_text is None else q_norm + " " + normalize_query(match_text)
    additions: list[str] = []
    for alias, official in get_synonyms().items():
        if alias in scan and official not in q_norm:
            additions.append(official)
    if not additions:
        return q_norm
    # 중복 토큰 제거하면서 순서 유지
    seen = set(q_norm.split())
    extra = [t for t in " ".join(additions).split() if t not in seen and not seen.add(t)]
    return q_norm + " " + " ".join(extra) if extra else q_norm
