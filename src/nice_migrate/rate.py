"""company_edge.trade_rate 계산·갱신 — 년단위 source(전파소스) 정규화.

trade_rate(from→to, year) = sly_amt(from→to, year) / Σ_out(from, year)
  Σ_out(from, year) = 그 해 from_bizno 가 내보낸 총 거래액.
  → 한 (from, year) 의 모든 outgoing rate 합 = 1 (source 정규화 = 충격 보존·수렴).

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
) -> dict:
    """trade_rate 를 source 정규화로 재계산·갱신. dry_run 이면 대상 수만 집계.

    alter_column=True(기본): 비율을 못 담는 컬럼 정의면 double precision 으로 보정.
    반환: {target_rows, updated, rate_sum_min/max/avg(검산)}.
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
    log.info(
        "trade_rate 갱신: %d 행 (year=%s). source정규화 검산 Σ_out min/max/avg=%.4f/%.4f/%.4f (≈1)",
        updated, year or "전체", chk[0] or 0, chk[1] or 0, chk[2] or 0,
    )
    return {
        "target_rows": int(target),
        "updated": int(updated),
        "rate_sum_min": float(chk[0] or 0),
        "rate_sum_max": float(chk[1] or 0),
        "rate_sum_avg": float(chk[2] or 0),
    }
