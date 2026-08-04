"""company_edge.trade_rate / sell_rate / buy_rate 계산·갱신 — 년단위 정규화.

trade_rate(from→to, year) = sly_amt(from→to, year) / Σ_out(from, year)
  Σ_out(from, year) = 그 해 from_bizno 가 내보낸 총 거래액.
  → 한 (from, year) 의 모든 outgoing rate 합 = 1 (source 정규화 = 충격 보존·수렴).

거래망 공유율(CRI 입력 = sell_share/buy_share, DB 컬럼명은 sell_rate/buy_rate):
  source → target = source 가 target 에게 판매.
  sell_rate(=sell_share) = sly_amt / Σ_out(source)  → source 매출 대비 target 판매 비중.
    (source 정규화라 trade_rate 와 동일 공식·동일 값. 이 거래 행 자신이 이미 source 의
    Σ_out 집계에 포함되므로 분모는 항상 >0 — "매입 기록만 있어 계산 안 되는" 경우는
    구조적으로 발생하지 않음. 대칭 fallback 불필요.)
  buy_rate (=buy_share)  = sly_amt / Σ_out(target)  → target 매출 대비 source 구매 비중.
    (target 매출로 정규화 → 상한 1 아님. target 이 무매출이면 0.)

buy_rate 대안 계산(옵션 fill_buy_fallback=True, 기본 False):
  target 자체 매출(Σ_out(target))이 없어 buy_rate 가 baseline 0 인 행을, target 매입 총액
  (Σ_in(target) = 그 target 으로 들어오는 모든 sly_amt 합 — 이 거래 자신도 포함되므로 분모
  항상 >0)으로 재계산. 의미가 "target 매출 대비"에서 "target 매입 총액 대비"로 바뀌므로
  buy_rate_basis 컬럼(target_sales/target_purchases)에 근거를 남겨 0(미계산)과 구분한다.
  CRI 등 하류 계산 결과에 영향을 주는 정의 변경이라 기본값은 off — 명시적으로 켜야 한다.

DB 의존 외부 패키지 없음(sqlalchemy 만) — 독립 CLI 로 배포 가능.
접속 정보: 인자 우선, 없으면 환경변수(POSTGRES_*) 폴백.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

log = logging.getLogger("nice_migrate")

_ENV = {  # 인자 미지정 시 폴백할 환경변수 (docker-compose 관례)
    "host": "POSTGRES_HOST",
    "port": "POSTGRES_PORT",
    "user": "POSTGRES_USER",
    "password": "POSTGRES_PASSWORD",
    "dbname": "POSTGRES_DB",
}
_DEFAULTS = {"host": "127.0.0.1", "port": "5432", "user": "nice", "password": "nice", "dbname": "nice_innovation"}


def load_env_file(path: str) -> None:
    """간단 .env 파서 — KEY=VALUE 줄을 os.environ 에 주입(의존성 없음). 따옴표/주석 처리."""
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def build_engine(
    *, dsn: str | None = None, host=None, port=None, user=None, password=None, dbname=None
) -> Engine:
    """dsn 직접 지정 또는 (host/port/user/password/dbname) → 환경변수 → 기본값 순."""
    if dsn:
        return create_engine(dsn, pool_pre_ping=True)
    given = {"host": host, "port": port, "user": user, "password": password, "dbname": dbname}
    # 우선순위: 인자 → 환경변수(POSTGRES_*) → 기본값
    parts = {k: (given[k] or os.getenv(_ENV[k]) or _DEFAULTS[k]) for k in _DEFAULTS}
    url = (
        f"postgresql+psycopg://{parts['user']}:{parts['password']}"
        f"@{parts['host']}:{parts['port']}/{parts['dbname']}"
    )
    return create_engine(url, pool_pre_ping=True)


# 년단위 source 정규화 UPDATE. year 지정 시 그 해만, None 이면 전체.
_UPDATE_SQL = """
    UPDATE {schema}.company_edge e
    SET trade_rate = CASE WHEN t.tot > 0 THEN e.sly_amt / t.tot ELSE 0 END
    FROM (
        SELECT from_bizno, trade_year, SUM(sly_amt)::numeric AS tot
        FROM {schema}.company_edge
        {where}
        GROUP BY from_bizno, trade_year
    ) t
    WHERE e.from_bizno = t.from_bizno AND e.trade_year = t.trade_year
      {where_e}
"""

_COUNT_SQL = "SELECT COUNT(*) FROM {schema}.company_edge {where}"
_VERIFY_SQL = """
    SELECT MIN(s), MAX(s), AVG(s) FROM (
        SELECT SUM(trade_rate)::float s FROM {schema}.company_edge {where}
        GROUP BY from_bizno, trade_year HAVING SUM(sly_amt) > 0
    ) q
"""

# 거래망 공유율(sell_rate/buy_rate) — 컬럼 존재 보장(신규 DB 대비). 이미 있으면 no-op.
# buy_rate_basis: buy_rate 계산 근거(target_sales=정상/target_purchases=대안 fallback/NULL=baseline 0).
_ENSURE_SHARE_COLS_SQL = """
    ALTER TABLE {schema}.company_edge
        ADD COLUMN IF NOT EXISTS sell_rate double precision,
        ADD COLUMN IF NOT EXISTS buy_rate double precision,
        ADD COLUMN IF NOT EXISTS buy_rate_basis text
"""

# 1단계: sell_rate = sly_amt/Σ_out(source=from) (= trade_rate). buy_rate=0/basis=NULL 로 baseline.
#   from 은 항상 판매자라 모든 대상 행이 매칭 → sell_rate·buy_rate baseline 전부 세팅.
_UPDATE_SELL_SQL = """
    UPDATE {schema}.company_edge e
    SET sell_rate = CASE WHEN t.tot > 0 THEN e.sly_amt / t.tot ELSE 0 END,
        buy_rate = 0,
        buy_rate_basis = NULL
    FROM (
        SELECT from_bizno, trade_year, SUM(sly_amt)::numeric AS tot
        FROM {schema}.company_edge
        {where}
        GROUP BY from_bizno, trade_year
    ) t
    WHERE e.from_bizno = t.from_bizno AND e.trade_year = t.trade_year
      {where_e}
"""

# 2단계: buy_rate = sly_amt/Σ_out(target=to). target 이 판매자(t.tot>0)인 행만 덮어씀.
#   무매출 target 은 매칭 안 돼 baseline 0/NULL 유지 → 3단계(옵션) fallback 대상으로 남음.
_UPDATE_BUY_SQL = """
    UPDATE {schema}.company_edge e
    SET buy_rate = e.sly_amt / t.tot,
        buy_rate_basis = 'target_sales'
    FROM (
        SELECT from_bizno, trade_year, SUM(sly_amt)::numeric AS tot
        FROM {schema}.company_edge
        {where}
        GROUP BY from_bizno, trade_year
    ) t
    WHERE e.to_bizno = t.from_bizno AND e.trade_year = t.trade_year AND t.tot > 0
      {where_e}
"""

# 3단계(옵션, fill_buy_fallback=True 일 때만): 2단계에서 못 채운(buy_rate_basis IS NULL) 행을
#   target 매입 총액(Σ_in(target)=to_bizno 기준 합)으로 재계산. 이 거래 자신도 Σ_in(target)
#   에 포함되므로 분모는 항상 >0(sly_amt>0 전제) — NULL/0-division 없음.
_UPDATE_BUY_FALLBACK_SQL = """
    UPDATE {schema}.company_edge e
    SET buy_rate = e.sly_amt / t.tot,
        buy_rate_basis = 'target_purchases'
    FROM (
        SELECT to_bizno, trade_year, SUM(sly_amt)::numeric AS tot
        FROM {schema}.company_edge
        {where}
        GROUP BY to_bizno, trade_year
    ) t
    WHERE e.to_bizno = t.to_bizno AND e.trade_year = t.trade_year AND t.tot > 0
      AND e.buy_rate_basis IS NULL
      {where_e}
"""

# dry-run 전용: buy_rate_basis 컬럼·UPDATE 없이, fallback 이 실제로 몇 행에 적용될지만 미리 집계.
#   target(to_bizno) 이 그 해 own 매출(t.tot>0)이 없는 행 수 = fallback 대상.
_PREVIEW_BUY_FALLBACK_SQL = """
    SELECT COUNT(*)
    FROM {schema}.company_edge e
    LEFT JOIN (
        SELECT from_bizno, trade_year, SUM(sly_amt)::numeric AS tot
        FROM {schema}.company_edge
        {where}
        GROUP BY from_bizno, trade_year
    ) t ON t.from_bizno = e.to_bizno AND t.trade_year = e.trade_year
    WHERE (t.tot IS NULL OR t.tot <= 0)
      {where_e}
"""

# 공유율 검산: sell_rate 는 trade_rate 와 동일(diff≈0), buy_rate 범위·근거별 건수 점검.
_VERIFY_SHARES_SQL = """
    SELECT MAX(ABS(sell_rate - trade_rate)),
           MIN(buy_rate), MAX(buy_rate), AVG(buy_rate),
           COUNT(*) FILTER (WHERE buy_rate_basis IS NULL),
           COUNT(*) FILTER (WHERE buy_rate_basis = 'target_sales'),
           COUNT(*) FILTER (WHERE buy_rate_basis = 'target_purchases')
    FROM {schema}.company_edge {where}
"""


def _ensure_rate_column(c, schema: str) -> bool:
    """trade_rate 가 비율(0~1)을 담을 수 있게 double precision 으로 보정.

    기존 스키마가 NUMERIC(3,6)(|값|<0.001) 처럼 비율을 못 담는 정의면 넓힌다.
    이미 double precision 이면 사실상 no-op. 반환: 변경 여부.
    """
    dtype = c.execute(
        text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema=:s AND table_name='company_edge' AND column_name='trade_rate'"
        ),
        {"s": schema},
    ).scalar_one_or_none()
    if dtype == "double precision":
        return False
    c.execute(
        text(f"ALTER TABLE {schema}.company_edge ALTER COLUMN trade_rate TYPE double precision")
    )
    log.info("trade_rate 컬럼 타입 %s → double precision 보정", dtype)
    return True


def update_trade_rate(
    engine: Engine,
    *,
    year: str | None = None,
    schema: str = "public",
    dry_run: bool = False,
    alter_column: bool = True,
    fill_shares: bool = True,
    fill_buy_fallback: bool = False,
) -> dict:
    """trade_rate 를 source 정규화로 재계산·갱신. dry_run 이면 대상 수만 집계.

    alter_column=True(기본): 비율을 못 담는 컬럼 정의면 double precision 으로 보정.
    fill_shares=True(기본): 같은 트랜잭션에서 sell_rate(=sell_share)/buy_rate(=buy_share) 도 채움.
    fill_buy_fallback=False(기본): target 이 무매출이라 buy_rate 가 0 인 행을 target 매입 총액
      기준으로 재계산(buy_rate_basis='target_purchases'). CRI 등 하류 계산에 영향을 주는
      정의 변경이므로 기본은 off — 명시적으로 켜야 적용된다. fill_shares=False 면 무시됨.
    반환: {target_rows, updated, rate_sum_min/max/avg(검산), shares_updated, sell_vs_rate_diff,
      buy_rate_min/max/avg, buy_rate_null(=basis 없음), buy_rate_from_target_sales,
      buy_rate_from_target_purchases(fallback 로 채워진 행 수)}.
    """
    where = "WHERE CAST(trade_year AS text) = :year" if year else ""
    where_e = "AND CAST(e.trade_year AS text) = :year" if year else ""
    params = {"year": str(year)} if year else {}
    with engine.begin() as c:
        target = c.execute(
            text(_COUNT_SQL.format(schema=schema, where=where)), params
        ).scalar_one()
        if dry_run:
            dry_out: dict = {"target_rows": int(target), "updated": 0, "dry_run": True}
            if fill_shares and fill_buy_fallback:
                would_fallback = c.execute(
                    text(_PREVIEW_BUY_FALLBACK_SQL.format(schema=schema, where=where, where_e=where_e)),
                    params,
                ).scalar_one()
                dry_out["buy_rate_fallback_would_update"] = int(would_fallback)
                log.info(
                    "[dry-run] 대상 행 %d (year=%s) — buy_rate fallback 적용 시 %d 행 영향 예상",
                    target, year or "전체", would_fallback,
                )
            else:
                log.info("[dry-run] 대상 행 %d (year=%s)", target, year or "전체")
            return dry_out
        if alter_column:
            _ensure_rate_column(c, schema)
        res = c.execute(
            text(_UPDATE_SQL.format(schema=schema, where=where, where_e=where_e)), params
        )
        updated = res.rowcount
        chk = c.execute(text(_VERIFY_SQL.format(schema=schema, where=where)), params).fetchone()
        out = {
            "target_rows": int(target),
            "updated": int(updated),
            "rate_sum_min": float(chk[0] or 0),
            "rate_sum_max": float(chk[1] or 0),
            "rate_sum_avg": float(chk[2] or 0),
        }
        if fill_shares:
            out.update(
                _fill_shares(
                    c, schema=schema, where=where, where_e=where_e, params=params,
                    buy_fallback=fill_buy_fallback,
                )
            )
    log.info(
        "trade_rate 갱신: %d 행 (year=%s). source정규화 검산 Σ_out min/max/avg=%.4f/%.4f/%.4f (≈1)",
        updated, year or "전체", chk[0] or 0, chk[1] or 0, chk[2] or 0,
    )
    return out


def _fill_shares(
    c, *, schema: str, where: str, where_e: str, params: dict, buy_fallback: bool = False
) -> dict:
    """sell_rate(=sell_share)/buy_rate(=buy_share) 를 채운다. 호출자 트랜잭션 재사용.

    sell_rate = sly_amt/Σ_out(source)  (trade_rate 와 동일), buy_rate = sly_amt/Σ_out(target).
    buy_fallback=True: target 무매출로 buy_rate 가 baseline 0 인 행을 target 매입 총액
      (Σ_in(target))으로 재계산(buy_rate_basis='target_purchases'). 기본 False(미적용).
    반환: 검산 지표 dict.
    """
    c.execute(text(_ENSURE_SHARE_COLS_SQL.format(schema=schema)))
    r_sell = c.execute(
        text(_UPDATE_SELL_SQL.format(schema=schema, where=where, where_e=where_e)), params
    )
    r_buy = c.execute(
        text(_UPDATE_BUY_SQL.format(schema=schema, where=where, where_e=where_e)), params
    )
    r_buy_fallback = None
    if buy_fallback:
        r_buy_fallback = c.execute(
            text(_UPDATE_BUY_FALLBACK_SQL.format(schema=schema, where=where, where_e=where_e)),
            params,
        )
    v = c.execute(text(_VERIFY_SHARES_SQL.format(schema=schema, where=where)), params).fetchone()
    fallback_note = f", buy_rate(target_purchases fallback) {r_buy_fallback.rowcount} 행" if r_buy_fallback else ""
    log.info(
        "공유율 갱신: sell_rate/baseline %d 행, buy_rate(target_sales) %d 행%s. "
        "검산 max|sell_rate-trade_rate|=%.2e (≈0), buy_rate min/max/avg=%.4f/%.4f/%.4f, "
        "basis 없음(미계산)=%d, target_sales=%d, target_purchases=%d",
        r_sell.rowcount, r_buy.rowcount, fallback_note,
        v[0] or 0, v[1] or 0, v[2] or 0, v[3] or 0, v[4] or 0, v[5] or 0, v[6] or 0,
    )
    return {
        "shares_updated": int(r_sell.rowcount),
        "buy_rate_updated": int(r_buy.rowcount),
        "buy_rate_fallback_updated": int(r_buy_fallback.rowcount) if r_buy_fallback else 0,
        "sell_vs_rate_diff": float(v[0] or 0),
        "buy_rate_min": float(v[1] or 0),
        "buy_rate_max": float(v[2] or 0),
        "buy_rate_avg": float(v[3] or 0),
        "buy_rate_null": int(v[4] or 0),
        "buy_rate_from_target_sales": int(v[5] or 0),
        "buy_rate_from_target_purchases": int(v[6] or 0),
    }
