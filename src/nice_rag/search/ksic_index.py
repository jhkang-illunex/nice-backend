"""KSIC hybrid 검색 — Reciprocal Rank Fusion(RRF) on 3 시그널.

``hsk_index`` 와 동일한 결합 구조 (설계 근거는 그쪽 모듈 docstring 참조)::

  vec : pgvector  (embedding <=> qvec)   ── 의미 매칭 (search_text 임베딩)
  trg : pg_trgm   (name_ko <-> q)        ── 분류 항목명 n-gram 부분일치
  ts  : tsvector  (ts_rank, OR-tsquery)  ── 토큰 매칭 (A=항목명 B=하위 항목명)

KSIC 특화 지점
  - 후보가 98 row(대분류 21 + 중분류 77) 뿐이라 pool 이 코퍼스의 절반을
    덮는다 — 시그널 하나만 살아도 후보가 풀에 남기 쉬워 hsk 보다 리콜이
    후하다. RRF 점수 스케일(1시그널 1위 ≈ 0.0164, 3시그널 만점 ≈ 0.0492)은
    hsk 와 동일.
  - '반도체' 류의 구체 업종어는 항목명(name_ko)에 없고 children_text
    (소·세·세세분류 명칭)에만 있으므로 trg 는 죽고 vec·ts 가 담당한다.
  - level 필터: 1=대분류, 2=중분류, None=전체.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from nice_common.db import get_pg_engine
from nice_rag.search.hsk_index import _vec_to_pg

# fmt: off
_HYBRID_SQL = text("""
WITH
  vec AS (
    SELECT code,
           ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:qvec AS vector)) AS rk
    FROM rag.ksic
    WHERE embedding IS NOT NULL
      AND (:level = 0 OR level = :level)
    ORDER BY embedding <=> CAST(:qvec AS vector)
    LIMIT :pool
  ),
  trg AS (
    SELECT code,
           ROW_NUMBER() OVER (ORDER BY name_ko <-> :qtext) AS rk
    FROM rag.ksic
    WHERE (:level = 0 OR level = :level)
    ORDER BY name_ko <-> :qtext
    LIMIT :pool
  ),
  q AS (
    -- plainto 의 AND(&) 를 OR(|) 로 변환. 빈 질의는 NULL tsquery → ts 0건
    SELECT to_tsquery('simple',
             NULLIF(replace(plainto_tsquery('simple', :qtext)::text, ' & ', ' | '), '')
           ) AS tsq
  ),
  ts AS (
    SELECT code,
           ROW_NUMBER() OVER (ORDER BY ts_rank(search_tsv, q.tsq) DESC) AS rk
    FROM rag.ksic, q
    WHERE search_tsv @@ q.tsq
      AND (:level = 0 OR level = :level)
    ORDER BY ts_rank(search_tsv, q.tsq) DESC
    LIMIT :pool
  ),
  ids AS (
    SELECT code FROM vec
    UNION SELECT code FROM trg
    UNION SELECT code FROM ts
  ),
  fused AS (
    SELECT ids.code,
           COALESCE(1.0 / (:rrf_k + vec.rk), 0)
         + COALESCE(1.0 / (:rrf_k + trg.rk), 0)
         + COALESCE(1.0 / (:rrf_k + ts.rk),  0) AS score
    FROM ids
    LEFT JOIN vec USING (code)
    LEFT JOIN trg USING (code)
    LEFT JOIN ts  USING (code)
  )
SELECT k.code, k.level, k.parent_code, k.name_ko, k.division_range,
       k.children_text, f.score AS score
FROM fused f
JOIN rag.ksic k USING (code)
ORDER BY f.score DESC, k.code ASC
LIMIT :limit
""")
# fmt: on


@dataclass(frozen=True)
class KsicHit:
    code: str
    level: int
    parent_code: str | None
    name_ko: str
    division_range: str | None
    children_text: str | None
    score: float


def search_hybrid(
    *,
    query_text: str,
    query_vec: list[float],
    limit: int = 10,
    pool: int = 50,
    rrf_k: int = 60,
    level: int | None = None,
) -> list[KsicHit]:
    """3 시그널 RRF 결합 결과를 score 내림차순으로 반환.

    Args:
      level: 1=대분류만, 2=중분류만. None 이면 두 계층 모두.
    """
    params = {
        "qtext": query_text,
        "qvec": _vec_to_pg(query_vec),
        "limit": limit,
        "pool": pool,
        "rrf_k": rrf_k,
        # named param 이 NULL 일 때 비교 회피 — 0 을 sentinel 로
        "level": level or 0,
    }
    with get_pg_engine().connect() as conn:
        rows = conn.execute(_HYBRID_SQL, params).mappings().all()
    return [
        KsicHit(
            code=r["code"],
            level=int(r["level"]),
            parent_code=r["parent_code"],
            name_ko=r["name_ko"],
            division_range=r["division_range"],
            children_text=r["children_text"],
            score=float(r["score"]),
        )
        for r in rows
    ]
