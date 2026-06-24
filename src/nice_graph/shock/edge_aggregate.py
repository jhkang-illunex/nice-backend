"""분기 부가세 신고 원천 → 연단위 거래 엣지 집계 (read-only, DB 무변경).

배경
----
운영 거래 원천 ``public.origin_itg_vat_dat`` 는 **분기 부가세 신고 단위**다.
한 거래쌍(공급기업→거래상대)이 한 해에 여러 분기·여러 신고차수 행으로 흩어져
있어, 전파 그래프가 쓰는 **연단위 엣지**로 합치고 **거래 비중(rate)** 을 다시
계산해야 한다. 이 모듈은 그 변환을 **메모리 계산으로만** 수행한다 — 어떤 테이블도
생성/수정/삭제하지 않고, 원천을 SELECT 만 한다.

원천 컬럼 매핑
--------------
  bizno               → from_bizno  (공급기업; Edge 방향 = 공급→구매, NICE 기준)
  trs_obj_bizrregno   → to_bizno    (거래상대)
  vat_stmt_yr         → trade_year  (신고연도 = 집계 단위)
  slyvl               → sly_amt     (공급가액)
  ttn_prid_st/end_date            (분기 기간 — 연단위로 롤업)
  vat_phs_rnu_divcd               (신고차수: '1'=예정신고, '2'=수정신고)

신고차수 처리 정책 (옵션)
-------------------------
같은 (from,to,연도,분기)에 1차/2차가 공존할 수 있어 합산 규칙을 선택해야 한다.
  REPLACE    : 분기별로 2차(수정신고)가 있으면 1차를 폐기하고 2차만 사용(없으면 1차).
               수정신고를 최종 확정값으로 보는 NICE '확정신고 우선' 취지에 부합.
  SUM_ALL    : 차수 구분 없이 모든 행 단순 합산.
  FIRST_ONLY : 1차(예정신고)만 합산, 2차 무시.

범위 밖(현재 미포함)
--------------------
  · 본점(RPS_BIZNO) 지점→대표기업 롤업 — 원천에 RPS_BIZNO 컬럼 없음(별도 매핑 필요).
  · 매출/매입 양측 신고 View 교차 중복제거 — 원천이 단일 View 라 해당 없음.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from enum import StrEnum

from sqlalchemy import text

from nice_common.db import get_pg_engine

_SRC_TABLE = "public.origin_itg_vat_dat"


class AmendmentPolicy(StrEnum):
    """신고차수(예정/수정) 합산 정책."""

    REPLACE = "replace"          # 옵션1: 분기별 2차 있으면 1차 폐기·2차 채택
    SUM_ALL = "sum_all"          # 옵션2: 차수 무관 전 행 합산
    FIRST_ONLY = "first_only"    # 1차(예정신고)만


@dataclass(frozen=True)
class YearlyEdge:
    """연단위로 합산된 거래 엣지 (공급→구매)."""

    from_bizno: str
    to_bizno: str
    trade_year: str
    sly_amt: float           # 연 공급가액 합
    n_filings: int           # 집계에 포함된 분기 신고 행 수
    rate: float | None = None  # source(from_bizno) 정규화 비중 (normalize=True 시)


# ── 신고차수 정책별 행 선택 SQL ────────────────────────────────────────────────
#
# REPLACE 는 (from,to,연도,분기) 그룹에서 최대 차수 행만 남긴다(2차>1차). 분기 내
# 같은 차수 복수행(복수 세금계산서)은 그대로 합산 — 분기 단위 '대체'이지 행 단위
# 중복제거가 아니다(완전중복 제거는 NICE Raw 단계 책임).
_ROW_CTE = {
    AmendmentPolicy.REPLACE: """
        ranked AS (
            SELECT f, t, y, slyvl,
                   MAX(chasu) OVER (PARTITION BY f, t, y, st, en) AS max_chasu,
                   chasu
            FROM base
        ),
        rows AS (SELECT f, t, y, slyvl FROM ranked WHERE chasu = max_chasu)
    """,
    AmendmentPolicy.SUM_ALL: """
        rows AS (SELECT f, t, y, slyvl FROM base)
    """,
    AmendmentPolicy.FIRST_ONLY: """
        rows AS (SELECT f, t, y, slyvl FROM base WHERE chasu = '1')
    """,
}


def _build_sql(policy: AmendmentPolicy, *, year: bool, firms: bool) -> str:
    """정책·필터에 맞는 집계 SQL 생성."""
    where = []
    if year:
        where.append("TRIM(vat_stmt_yr) = :yr")
    if firms:
        where.append("TRIM(bizno) = ANY(:firms)")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return f"""
        WITH base AS (
            SELECT TRIM(bizno)             AS f,
                   TRIM(trs_obj_bizrregno) AS t,
                   TRIM(vat_stmt_yr)       AS y,
                   TRIM(ttn_prid_st_date)  AS st,
                   TRIM(ttn_prid_end_date) AS en,
                   TRIM(vat_phs_rnu_divcd) AS chasu,
                   CAST(COALESCE(slyvl, 0) AS FLOAT) AS slyvl
            FROM {_SRC_TABLE}
            {where_sql}
        ),
        {_ROW_CTE[policy]}
        SELECT f, t, y, CAST(SUM(slyvl) AS FLOAT) AS sly_amt, COUNT(*) AS n_filings
        FROM rows
        WHERE f <> '' AND t <> '' AND y <> ''
        GROUP BY f, t, y
        HAVING SUM(slyvl) > 0
    """


def aggregate_yearly_edges(
    *,
    policy: AmendmentPolicy = AmendmentPolicy.REPLACE,
    trade_year: str | None = None,
    only_biznos: tuple[str, ...] | None = None,
    normalize: bool = False,
    engine=None,
) -> list[YearlyEdge]:
    """분기 신고 원천을 연단위 거래 엣지로 집계 (read-only).

    Args:
      policy: 신고차수 합산 정책(REPLACE/SUM_ALL/FIRST_ONLY).
      trade_year: 특정 연도만(None=전 연도 각각 집계).
      only_biznos: 공급기업(from) 한정 — None=전체.
      normalize: True 면 rate = sly_amt / Σ_out(from_bizno, 같은 연도) 를 채운다.
                 (전체 결과집합 기준 source 정규화 = 글로벌 거래 비중. 서브그래프
                 한정 정규화는 assemble 단계가 별도로 수행.)
      engine: SQLAlchemy Engine 주입(테스트용). None=운영 PG.

    Returns:
      YearlyEdge 리스트. (from_bizno, to_bizno, trade_year) 유일.
    """
    eng = engine or get_pg_engine()
    sql = text(_build_sql(policy, year=trade_year is not None, firms=only_biznos is not None))
    params: dict[str, object] = {}
    if trade_year is not None:
        params["yr"] = str(trade_year)
    if only_biznos is not None:
        params["firms"] = list(only_biznos)

    with eng.connect() as c:
        raw = c.execute(sql, params).mappings().all()

    edges = [
        YearlyEdge(
            from_bizno=r["f"],
            to_bizno=r["t"],
            trade_year=r["y"],
            sly_amt=r["sly_amt"],
            n_filings=int(r["n_filings"]),
        )
        for r in raw
    ]
    if normalize:
        edges = _attach_source_rate(edges)
    return edges


def _attach_source_rate(edges: list[YearlyEdge]) -> list[YearlyEdge]:
    """각 (from_bizno, trade_year) 의 outgoing 합으로 rate 재계산 → 거래 비중.

    rate = sly_amt / Σ_out(from, year). 연·기업 묶음마다 분모를 다시 잡으므로,
    연단위로 합쳐진 금액 기준으로 '기업별 거래처 비중' 이 일관되게 재산출된다.
    """
    denom: dict[tuple[str, str], float] = defaultdict(float)
    for e in edges:
        denom[(e.from_bizno, e.trade_year)] += e.sly_amt
    out = []
    for e in edges:
        d = denom[(e.from_bizno, e.trade_year)]
        out.append(replace(e, rate=(e.sly_amt / d) if d > 0 else 0.0))
    return out
