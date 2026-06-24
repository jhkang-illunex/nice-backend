"""DEPRECATED — 데모는 s_ra603 으로 옮겨감 (``nice_demo.queries.s_ra603``).
이 모듈은 *국가비중* 메타 (ra604) 가 향후 필요해질 때 참고용으로 유지.

origin_kis_em__s_em001 (기업 마스터) ⋈ origin_kis_ra__s_ra604 (HS×국가 통계).

운영 PG 의 실제 적재 구조::

    public.origin_kis_em__s_em001   bizno · upchecd(HS6) · korentrnm · ...
    public.origin_kis_ra__s_ra604   bse_yr · upchecd(HS6) · tseximdivcd · tsstdnatcd · tstrdwgt

조인 키 = ``upchecd`` (HS6).  RAG 검색은 10자리 HS 를 돌려주므로 ``LEFT(hs10, 6)``
로 HS6 변환 후 매칭한다.

데이터 한계 (운영 실데이터 적재 전 상태)
  s_em001 = 3행 (현대모비스/삼성전기/포스코). HS6 380130 만 정상 형식.
  s_ra604 = 전국 HS×국가 통계, 행수는 충분.

운영 실데이터 적재 후엔 SQL 한 줄도 안 바뀜 — 행수만 커진다.
"""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import text

from nice_common.db import get_pg_engine

log = logging.getLogger(__name__)


_EM_TABLE = "public.origin_kis_em__s_em001"
_RA_TABLE = "public.origin_kis_ra__s_ra604"


def _hs6(hs_code10: str) -> str:
    """RAG 가 돌려주는 10자리 HS → s_em001/s_ra604 의 6자리 upchecd."""
    return (hs_code10 or "").strip()[:6]


_SEED_SQL = text(
    f"""
    WITH base AS (
        SELECT upchecd, tsstdnatcd, tseximdivcd, tstrdwgt,
               tstrdwgt / NULLIF(
                   SUM(tstrdwgt) OVER (PARTITION BY upchecd, tseximdivcd),
                   0
               ) AS ratio
        FROM {_RA_TABLE}
        WHERE upchecd = :hs6
          AND CAST(bse_yr AS text) = :trade_year
    ),
    country_mix AS (
        SELECT
            upchecd,
            jsonb_agg(
                jsonb_build_object(
                    'cnty', tsstdnatcd,
                    'dir',  tseximdivcd,
                    'wgt',  tstrdwgt,
                    'ratio', ratio
                )
                ORDER BY tstrdwgt DESC NULLS LAST
            ) AS country_mix,
            SUM(CASE WHEN tseximdivcd = '1' THEN tstrdwgt ELSE 0 END) AS imp_amt,
            SUM(CASE WHEN tseximdivcd = '2' THEN tstrdwgt ELSE 0 END) AS exp_amt
        FROM base
        GROUP BY upchecd
    )
    SELECT
        em.bizno,
        em.upchecd,
        em.korentrnm,
        em.engentrnm,
        em.korreprnm,
        COALESCE(cm.imp_amt, 0)::float AS imp_amt,
        COALESCE(cm.exp_amt, 0)::float AS exp_amt,
        COALESCE(cm.country_mix, '[]'::jsonb) AS country_mix
    FROM {_EM_TABLE} em
    LEFT JOIN country_mix cm ON em.upchecd = cm.upchecd
    WHERE em.upchecd = :hs6
    """
)


def fetch_seeds(hs_code10: str, trade_year: str) -> pd.DataFrame:
    """선택된 HS10 + 연도 → 시드 bizno + (HS 단위) 국가비중 메타.

    Returns DataFrame[bizno, upchecd, korentrnm, engentrnm, korreprnm,
                      imp_amt, exp_amt, country_mix(list[dict])].

    Note
    ----
    country_mix 는 *HS6 단위* 의 전국 통계 — 기업별이 아니라 해당 HS 전체.
    데모 PoC 의 의도 (`해외에서 오는 임팩트` 컨텍스트) 에는 충분.
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
    WITH base AS (
        SELECT upchecd, tsstdnatcd, tseximdivcd, tstrdwgt,
               tstrdwgt / NULLIF(
                   SUM(tstrdwgt) OVER (PARTITION BY upchecd, tseximdivcd),
                   0
               ) AS ratio
        FROM {_RA_TABLE}
        WHERE CAST(bse_yr AS text) = :trade_year
    ),
    country_mix AS (
        SELECT
            upchecd,
            jsonb_agg(
                jsonb_build_object(
                    'cnty', tsstdnatcd,
                    'dir',  tseximdivcd,
                    'wgt',  tstrdwgt,
                    'ratio', ratio
                )
                ORDER BY tstrdwgt DESC NULLS LAST
            ) AS country_mix,
            SUM(CASE WHEN tseximdivcd = '1' THEN tstrdwgt ELSE 0 END) AS imp_amt,
            SUM(CASE WHEN tseximdivcd = '2' THEN tstrdwgt ELSE 0 END) AS exp_amt
        FROM base
        GROUP BY upchecd
    )
    SELECT
        em.bizno,
        em.upchecd,
        em.korentrnm,
        COALESCE(cm.imp_amt, 0)::float AS imp_amt,
        COALESCE(cm.exp_amt, 0)::float AS exp_amt,
        COALESCE(cm.country_mix, '[]'::jsonb) AS country_mix
    FROM {_EM_TABLE} em
    LEFT JOIN country_mix cm ON em.upchecd = cm.upchecd
    WHERE em.bizno = ANY(:biznos)
    """
)


def fetch_company_mix(bizno_list: list[str], trade_year: str) -> pd.DataFrame:
    """확장된 모든 노드 → 각 bizno 가 다루는 HS 의 국가비중 메타.

    BFS 확장으로 발견된 노드 중 s_em001 에 등재 안 된 bizno 는 자동 제외.
    LLM 입력에서는 country_mix 가 [] 로 들어가도 안전.
    """
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
    """UI 의 연도 selectbox 옵션 — s_ra604.bse_yr 에서 distinct."""
    sql = text(f"SELECT DISTINCT bse_yr FROM {_RA_TABLE} ORDER BY bse_yr DESC")
    try:
        with get_pg_engine().connect() as conn:
            rows = list(conn.execute(sql).scalars())
        return [str(y) for y in rows if y is not None]
    except Exception:
        log.exception("available_years failed (table missing or perms)")
        return []
