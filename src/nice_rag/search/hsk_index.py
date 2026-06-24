"""HSK hybrid 검색 — Reciprocal Rank Fusion(RRF) on 3 시그널.

시그널
  vec : pgvector  (embedding <=> qvec)   ── 의미 매칭 (search_text 임베딩)
  trg : pg_trgm   (name_ko <-> q)        ── 품목명 n-gram 부분일치 (오타/짧은 한글)
  ts  : tsvector  (ts_rank, OR-tsquery)  ── 토큰 매칭 (가중: A=품목명 B=계층 C=분류 D=조항)

ts 가 AND(plainto)가 아닌 OR 결합인 이유: 자연어/동의어 확장 질의는 색인에
없는 토큰('순수', 'BEV', 조사 잔여 등)을 흔히 포함한다. AND 는 토큰 하나만
없어도 ts 시그널 전체가 죽고, 동의어 확장이 토큰을 덧붙일수록 오히려 악화
된다 (실측: 'BEV' 질의에서 정답 8703.80 이 ts 0건 → 22위). OR 는 맞춘
토큰 수·가중치만큼 ts_rank 가 오르므로 부분 일치 질의에 강건하다.

trg 를 search_text 가 아닌 name_ko 에 거는 이유: trigram 전체 문자열 유사도는
자카드 기반이라 긴 문서일수록 희석된다 — detail chain 이 포함된 search_text
에서는 짧은 질의가 '짧은 오답 문서' 와 더 유사해지는 길이 편향이 발생
(실측: 정답이 trg 211위까지 밀림). 짧고 변별력 있는 품목명이 적합하고,
계층/조항 텍스트 커버리지는 ts·vec 시그널이 담당한다.

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

from nice_common.db import get_pg_engine

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
           ROW_NUMBER() OVER (ORDER BY name_ko <-> :qtext) AS rk
    FROM rag.hsk
    WHERE name_ko IS NOT NULL
      AND (:active_only = false OR valid_to >= CURRENT_DATE)
      AND (:hs_prefix_like = '' OR hs_code LIKE :hs_prefix_like)
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
    SELECT hs_code,
           ROW_NUMBER() OVER (ORDER BY ts_rank(search_tsv, q.tsq) DESC) AS rk
    FROM rag.hsk, q
    WHERE search_tsv @@ q.tsq
      AND (:active_only = false OR valid_to >= CURRENT_DATE)
      AND (:hs_prefix_like = '' OR hs_code LIKE :hs_prefix_like)
    -- ORDER BY 없는 LIMIT 은 임의 행을 잘라 ts_rank 상위 후보가 풀에서 탈락할 수 있음
    ORDER BY ts_rank(search_tsv, q.tsq) DESC
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
