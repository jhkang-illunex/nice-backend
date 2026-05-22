"""PostgreSQL upsert sink.

INSERT … ON CONFLICT (pk) DO UPDATE 를 통한 idempotent 적재.
psycopg3 ``cursor.executemany`` 가 내부 배치 → 1만 건 수준은 충분.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from nice_poc.db import get_pg_engine


def _placeholders(cols: Sequence[str]) -> str:
    return ", ".join(f":{c}" for c in cols)


def _build_upsert_sql(table: str, cols: Sequence[str], pk: Sequence[str]) -> str:
    update_cols = [c for c in cols if c not in pk]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols) or "/* nothing */"
    conflict = ", ".join(pk)
    return (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({_placeholders(cols)}) "
        f"ON CONFLICT ({conflict}) DO UPDATE SET {set_clause}"
    )


def _chunks(rows: Sequence[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield list(rows[i : i + size])


def _df_to_rows(df: pd.DataFrame, cols: Sequence[str]) -> list[dict[str, Any]]:
    sub = df.reindex(columns=cols)
    return sub.where(sub.notna(), None).to_dict("records")


class PgSink:
    def __init__(self, engine: Engine | None = None, batch_size: int = 1000) -> None:
        self.engine = engine or get_pg_engine()
        self.batch_size = batch_size

    def upsert(
        self,
        table: str,
        df: pd.DataFrame,
        *,
        pk: Sequence[str],
        columns: Sequence[str] | None = None,
    ) -> int:
        if df.empty:
            return 0
        cols = list(columns) if columns is not None else list(df.columns)
        rows = _df_to_rows(df, cols)
        sql = text(_build_upsert_sql(table, cols, pk))

        total = 0
        with self.engine.begin() as conn:
            for chunk in _chunks(rows, self.batch_size):
                conn.execute(sql, chunk)
                total += len(chunk)
        return total

    def refresh_mv(self, name: str, *, concurrently: bool = True) -> None:
        kw = "CONCURRENTLY" if concurrently else ""
        with self.engine.begin() as conn:
            conn.execute(text(f"REFRESH MATERIALIZED VIEW {kw} {name}"))

    def count(self, table: str) -> int:
        with self.engine.connect() as conn:
            n = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        return int(n)

    def truncate(self, *tables: str) -> None:
        if not tables:
            return
        joined = ", ".join(tables)
        with self.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))


__all__ = ["PgSink"]
