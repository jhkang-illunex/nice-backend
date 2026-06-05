"""HSK hybrid 검색 — Reciprocal Rank Fusion(RRF) on 3 시그널.

시그널
  vec : pgvector  (embedding <=> qvec)   ── 의미 매칭
  trg : pg_trgm   (search_text <-> q)    ── n-gram 부분일치 (오타/짧은 한글)
  ts  : tsvector  (ts_rank with plainto) ── 토큰 매칭

각 시그널의 rank 를 RRF 점수로 결합::

    score = Σ 1 / (rrf_k + rank_i)

RRF (Cormack et al. 2009) 는 시그널 간 score 정규화 없이도 안정적으로 fuse —
임베딩 cosine, trgm distance, ts_rank 처럼 단위가 전혀 다른 점수들을 합칠 때
일반적인 weighted sum 보다 견고하다. ``rrf_k`` 기본 60 은 정보검색 표준값.

단일 SQL CTE 로 1-roundtrip. pool=50, limit=10 기본 — pool 은 RRF 가 검토하는
후보 풀, limit 은 최종 응답 수.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text

from nice_poc.db import get_pg_engine

# fmt: off
_HYBRID_SQL = text("""
WITH
  vec AS (
    SELECT hs_code,
           ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:qvec AS vector)) AS rk
    FROM rag.hsk
    WHERE embedding IS NOT NULL
      AND (:active_only = false OR valid_to >= CURRENT_DATE)
      AND (:hs_prefix_like = '' OR hs_code LIKE :hs_prefix_like)
    ORDER BY embedding <=> CAST(:qvec AS vector)
    LIMIT :pool
  ),
  trg AS (
    SELECT hs_code,
           ROW_NUMBER() OVER (ORDER BY search_text <-> :qtext) AS rk
    FROM rag.hsk
    WHERE search_text IS NOT NULL
      AND (:active_only = false OR valid_to >= CURRENT_DATE)
      AND (:hs_prefix_like = '' OR hs_code LIKE :hs_prefix_like)
    ORDER BY search_text <-> :qtext
    LIMIT :pool
  ),
  ts AS (
    SELECT hs_code,
           ROW_NUMBER() OVER (ORDER BY ts_rank(search_tsv, plainto_tsquery('simple', :qtext)) DESC) AS rk
    FROM rag.hsk
    WHERE search_tsv @@ plainto_tsquery('simple', :qtext)
      AND (:active_only = false OR valid_to >= CURRENT_DATE)
      AND (:hs_prefix_like = '' OR hs_code LIKE :hs_prefix_like)
    LIMIT :pool
  ),
  ids AS (
    SELECT hs_code FROM vec
    UNION SELECT hs_code FROM trg
    UNION SELECT hs_code FROM ts
  ),
  fused AS (
    SELECT ids.hs_code,
           COALESCE(1.0 / (:rrf_k + vec.rk), 0)
         + COALESCE(1.0 / (:rrf_k + trg.rk), 0)
         + COALESCE(1.0 / (:rrf_k + ts.rk),  0) AS score
    FROM ids
    LEFT JOIN vec USING (hs_code)
    LEFT JOIN trg USING (hs_code)
    LEFT JOIN ts  USING (hs_code)
  )
SELECT h.hs_code, h.name_ko, h.name_en, h.search_text AS description, f.score AS score
FROM fused f
JOIN rag.hsk h USING (hs_code)
ORDER BY f.score DESC, h.hs_code ASC
LIMIT :limit
""")
# fmt: on


@dataclass(frozen=True)
class HybridHit:
    hs_code: str
    name_ko: str | None
    name_en: str | None
    description: str | None
    score: float


def _vec_to_pg(vec: list[float]) -> str:
    """pgvector textual 표현 — pgvector-python 의존 없이 동작."""
    # PG vector parser 가 받는 형식: '[0.1,0.2,...]'
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def search_hybrid(
    *,
    query_text: str,
    query_vec: list[float],
    limit: int = 10,
    pool: int = 50,
    rrf_k: int = 60,
    active_only: bool = False,
    hs_prefix: str | None = None,
) -> list[HybridHit]:
    """3 시그널 RRF 결합 결과를 score 내림차순으로 반환.

    Args:
      hs_prefix: HS2/HS4/HS6 등 prefix 로 검색 범위 제한. None 이면 전체.
    """
    params = {
        "qtext": query_text,
        "qvec": _vec_to_pg(query_vec),
        "limit": limit,
        "pool": pool,
        "rrf_k": rrf_k,
        "active_only": active_only,
        # named param 이 NULL 일 때 LIKE 회피 — 빈문자열을 sentinel 로
        "hs_prefix_like": f"{hs_prefix}%" if hs_prefix else "",
    }
    with get_pg_engine().connect() as conn:
        rows = conn.execute(_HYBRID_SQL, params).mappings().all()
    return [
        HybridHit(
            hs_code=r["hs_code"],
            name_ko=r["name_ko"],
            name_en=r["name_en"],
            description=r["description"],
            score=float(r["score"]),
        )
        for r in rows
    ]
