"""Neo4j MERGE sink — UNWIND 기반 idempotent 적재.

배치 단위로 트랜잭션을 끊어 1만+ 행에서도 메모리 안전.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd

from nice_poc.db import neo4j_session


def _chunks(rows: Sequence[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield list(rows[i : i + size])


def _df_to_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    return df.where(df.notna(), None).to_dict("records")


class Neo4jSink:
    def __init__(self, database: str | None = None, batch_size: int = 1000) -> None:
        self.database = database
        self.batch_size = batch_size

    def run_unwind(self, cypher: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        rows = _df_to_rows(df)
        total = 0
        with neo4j_session(database=self.database) as s:
            for chunk in _chunks(rows, self.batch_size):
                s.run(cypher, rows=chunk).consume()
                total += len(chunk)
        return total

    def count(self, cypher: str) -> int:
        with neo4j_session(database=self.database) as s:
            rec = s.run(cypher).single()
        return int(next(iter(rec.values()))) if rec else 0


__all__ = ["Neo4jSink"]
