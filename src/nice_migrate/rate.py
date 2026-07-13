"""company_edge.trade_rate / sell_rate / buy_rate 계산·갱신 — 년단위 정규화.

trade_rate(from→to, year) = sly_amt(from→to, year) / Σ_out(from, year)
  Σ_out(from, year) = 그 해 from_bizno 가 내보낸 총 거래액.
  → 한 (from, year) 의 모든 outgoing rate 합 = 1 (source 정규화 = 충격 보존·수렴).

거래망 공유율(CRI 입력 = sell_share/buy_share, DB 컬럼명은 sell_rate/buy_rate):
  source → target = source 가 target 에게 판매.
  sell_rate(=sell_share) = sly_amt / Σ_out(source)  → source 매출 대비 target 판매 비중.
    (source 정규화라 trade_rate 와 동일 공식·동일 값.)
  buy_rate (=buy_share)  = sly_amt / Σ_out(target)  → target 매출 대비 source 구매 비중.
    (target 매출로 정규화 → 상한 1 아님. target 이 무매출이면 0.)

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
_ENSURE_SHARE_COLS_SQL = """
    ALTER TABLE {schema}.company_edge
        ADD COLUMN IF NOT EXISTS sell_rate double precision,
        ADD COLUMN IF NOT EXISTS buy_rate double precision
"""

# 1단계: sell_rate = sly_amt/Σ_out(source=from) (= trade_rate). buy_rate=0 으로 baseline.
#   from 은 항상 판매자라 모든 대상 행이 매칭 → sell_rate·buy_rate baseline 전부 세팅.
_UPDATE_SELL_SQL = """
    UPDATE {schema}.company_edge e
    SET sell_rate = CASE WHEN t.tot > 0 THEN e.sly_amt / t.tot ELSE 0 END,
        buy_rate = 0
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
#   무매출 target 은 매칭 안 돼 baseline 0 유지 → NULL 방지.
_UPDATE_BUY_SQL = """
    UPDATE {schema}.company_edge e
    SET buy_rate = e.sly_amt / t.tot
    FROM (
        SELECT from_bizno, trade_year, SUM(sly_amt)::numeric AS tot
        FROM {schema}.company_edge
        {where}
        GROUP BY from_bizno, trade_year
    ) t
    WHERE e.to_bizno = t.from_bizno AND e.trade_year = t.trade_year AND t.tot > 0
      {where_e}
"""

# 공유율 검산: sell_rate 는 trade_rate 와 동일(diff≈0), buy_rate 범위·NULL 점검.
_VERIFY_SHARES_SQL = """
    SELECT MAX(ABS(sell_rate - trade_rate)),
           MIN(buy_rate), MAX(buy_rate), AVG(buy_rate),
           COUNT(*) FILTER (WHERE buy_rate IS NULL)
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
) -> dict:
    """trade_rate 를 source 정규화로 재계산·갱신. dry_run 이면 대상 수만 집계.

    alter_column=True(기본): 비율을 못 담는 컬럼 정의면 double precision 으로 보정.
    fill_shares=True(기본): 같은 트랜잭션에서 sell_rate(=sell_share)/buy_rate(=buy_share) 도 채움.
    반환: {target_rows, updated, rate_sum_min/max/avg(검산), shares_updated, sell_vs_rate_diff, buy_min/max/avg}.
    """
    where = "WHERE CAST(trade_year AS text) = :year" if year else ""
    where_e = "AND CAST(e.trade_year AS text) = :year" if year else ""
    params = {"year": str(year)} if year else {}
    with engine.begin() as c:
        target = c.execute(
            text(_COUNT_SQL.format(schema=schema, where=where)), params
        ).scalar_one()
        if dry_run:
            log.info("[dry-run] 대상 행 %d (year=%s)", target, year or "전체")
            return {"target_rows": int(target), "updated": 0, "dry_run": True}
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
            out.update(_fill_shares(c, schema=schema, where=where, where_e=where_e, params=params))
    log.info(
        "trade_rate 갱신: %d 행 (year=%s). source정규화 검산 Σ_out min/max/avg=%.4f/%.4f/%.4f (≈1)",
        updated, year or "전체", chk[0] or 0, chk[1] or 0, chk[2] or 0,
    )
    return out


def _fill_shares(c, *, schema: str, where: str, where_e: str, params: dict) -> dict:
    """sell_rate(=sell_share)/buy_rate(=buy_share) 를 채운다. 호출자 트랜잭션 재사용.

    sell_rate = sly_amt/Σ_out(source)  (trade_rate 와 동일), buy_rate = sly_amt/Σ_out(target).
    반환: 검산 지표 dict.
    """
    c.execute(text(_ENSURE_SHARE_COLS_SQL.format(schema=schema)))
    r_sell = c.execute(
        text(_UPDATE_SELL_SQL.format(schema=schema, where=where, where_e=where_e)), params
    )
    r_buy = c.execute(
        text(_UPDATE_BUY_SQL.format(schema=schema, where=where, where_e=where_e)), params
    )
    v = c.execute(text(_VERIFY_SHARES_SQL.format(schema=schema, where=where)), params).fetchone()
    log.info(
        "공유율 갱신: sell_rate/baseline %d 행, buy_rate %d 행. "
        "검산 max|sell_rate-trade_rate|=%.2e (≈0), buy_rate min/max/avg=%.4f/%.4f/%.4f, null=%d",
        r_sell.rowcount, r_buy.rowcount, v[0] or 0, v[1] or 0, v[2] or 0, v[3] or 0, v[4] or 0,
    )
    return {
        "shares_updated": int(r_sell.rowcount),
        "buy_rate_updated": int(r_buy.rowcount),
        "sell_vs_rate_diff": float(v[0] or 0),
        "buy_rate_min": float(v[1] or 0),
        "buy_rate_max": float(v[2] or 0),
        "buy_rate_avg": float(v[3] or 0),
        "buy_rate_null": int(v[4] or 0),
    }
