"""origin_kis_em__s_em001 (기업 마스터) ⋈ origin_kis_ra__s_ra603 (HS×MTI 분포).

조인 키 = ``upchecd`` (HS6). RAG 가 돌려주는 10자리 HS → ``LEFT(hs10, 6)``.

s_ra603 의 실 컬럼 (운영 PG 검증)
  bse_yr            기준연도 (2023~2026)
  upchecd           HS6
  tseximdivcd       0 / 3 — 수출입 구분 (KIS 내부 코드, 0=수입·3=수출 추정)
  tscdcg            코드 카테고리: H10·H6·M3·M4·M6·ALL
                    (H? = HS N자리, M? = MTI 산업분류 N자리, ALL = 총계)
  tscdvl            코드 카테고리 안의 코드 값
  tstrdwgt          비중 (%) — ALL 행은 100
  tstotusdamtstncd  USD 환산 코드
  data_rgs_date    데이터 등록일

ra604 (국가비중) 와 달리 *국가 차원이 없음* — ra603 은 *산업분류(MTI) 분포* 가
주된 통계. 반환 컬럼명 ``country_mix`` 는 호환성 유지 위해 그대로 두되 내부
구조는 `(cdcg, cdvl, wgt, dir)` 객체 리스트로 변경. LLM 은 raw JSON 으로
받아도 해석 가능.
"""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import text

from nice_common.db import get_pg_engine

log = logging.getLogger(__name__)


_EM_TABLE = "public.origin_kis_em__s_em001"
_RA_TABLE = "public.origin_kis_ra__s_ra603"


def _hs6(hs_code10: str) -> str:
    """RAG 가 돌려주는 10자리 HS → s_em001/s_ra603 의 6자리 upchecd."""
    return (hs_code10 or "").strip()[:6]


_SEED_SQL = text(
    f"""
    WITH meta AS (
        SELECT
            upchecd,
            SUM(CASE WHEN tseximdivcd = '0' AND tscdcg = 'ALL' THEN tstrdwgt ELSE 0 END) AS imp_amt,
            SUM(CASE WHEN tseximdivcd = '3' AND tscdcg = 'ALL' THEN tstrdwgt ELSE 0 END) AS exp_amt,
            jsonb_agg(
                jsonb_build_object(
                    'cdcg', tscdcg,
                    'cdvl', tscdvl,
                    'wgt',  tstrdwgt,
                    'dir',  tseximdivcd
                )
                ORDER BY tstrdwgt DESC NULLS LAST
            ) AS country_mix
        FROM {_RA_TABLE}
        WHERE upchecd = :hs6
          AND CAST(bse_yr AS text) = :trade_year
        GROUP BY upchecd
    )
    SELECT
        em.bizno,
        em.upchecd,
        em.korentrnm,
        em.engentrnm,
        em.korreprnm,
        COALESCE(m.imp_amt, 0)::float AS imp_amt,
        COALESCE(m.exp_amt, 0)::float AS exp_amt,
        COALESCE(m.country_mix, '[]'::jsonb) AS country_mix
    FROM {_EM_TABLE} em
    LEFT JOIN meta m ON em.upchecd = m.upchecd
    WHERE em.upchecd = :hs6
    """
)


def fetch_seeds(hs_code10: str, trade_year: str) -> pd.DataFrame:
    """선택된 HS10 + 연도 → 시드 bizno + (HS 단위) 산업분류 비중 메타.

    Returns DataFrame[bizno, upchecd, korentrnm, engentrnm, korreprnm,
                      imp_amt, exp_amt, country_mix(list[dict])].
    """
    hs6 = _hs6(hs_code10)
    with get_pg_engine().connect() as conn:
        return pd.read_sql_query(
            _SEED_SQL,
            conn,
            params={"hs6": hs6, "trade_year": str(trade_year)},
        )


_COMPANY_MIX_SQL = text(
    f"""
    WITH meta AS (
        SELECT
            upchecd,
            SUM(CASE WHEN tseximdivcd = '0' AND tscdcg = 'ALL' THEN tstrdwgt ELSE 0 END) AS imp_amt,
            SUM(CASE WHEN tseximdivcd = '3' AND tscdcg = 'ALL' THEN tstrdwgt ELSE 0 END) AS exp_amt,
            jsonb_agg(
                jsonb_build_object(
                    'cdcg', tscdcg,
                    'cdvl', tscdvl,
                    'wgt',  tstrdwgt,
                    'dir',  tseximdivcd
                )
                ORDER BY tstrdwgt DESC NULLS LAST
            ) AS country_mix
        FROM {_RA_TABLE}
        WHERE CAST(bse_yr AS text) = :trade_year
        GROUP BY upchecd
    )
    SELECT
        em.bizno,
        em.upchecd,
        em.korentrnm,
        COALESCE(m.imp_amt, 0)::float AS imp_amt,
        COALESCE(m.exp_amt, 0)::float AS exp_amt,
        COALESCE(m.country_mix, '[]'::jsonb) AS country_mix
    FROM {_EM_TABLE} em
    LEFT JOIN meta m ON em.upchecd = m.upchecd
    WHERE em.bizno = ANY(:biznos)
    """
)


def fetch_company_mix(bizno_list: list[str], trade_year: str) -> pd.DataFrame:
    """확장된 노드 → 각 bizno 가 다루는 HS 의 산업분류 비중 메타."""
    if not bizno_list:
        return pd.DataFrame(
            columns=["bizno", "upchecd", "korentrnm", "imp_amt", "exp_amt", "country_mix"]
        )
    with get_pg_engine().connect() as conn:
        return pd.read_sql_query(
            _COMPANY_MIX_SQL,
            conn,
            params={"biznos": list(bizno_list), "trade_year": str(trade_year)},
        )


def available_years() -> list[str]:
    """UI 의 연도 selectbox 옵션 — s_ra603.bse_yr 에서 distinct."""
    sql = text(f"SELECT DISTINCT bse_yr FROM {_RA_TABLE} ORDER BY bse_yr DESC")
    try:
        with get_pg_engine().connect() as conn:
            rows = list(conn.execute(sql).scalars())
        return [str(y) for y in rows if y is not None]
    except Exception:
        log.exception("available_years failed (table missing or perms)")
        return []
