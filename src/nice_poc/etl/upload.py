"""임의 CSV → PostgreSQL / Neo4j 업로드.

도메인 파이프라인(``etl.pipelines``)이 정해진 스키마를 가정하는 반면,
본 모듈은 **임의 CSV** 를 받아 컬럼명 매핑·dtype 추론·dry-run 을 지원한다.

사용 예 (Python)::

    from nice_poc.etl.upload import upload_to_pg, upload_to_neo4j

    upload_to_pg(
        "/data/firms_kor.csv",
        table="firms",
        pk=["firm_id"],
        rename={"기업ID": "firm_id", "기업명": "firm_name"},
    )

    upload_to_neo4j(
        "/data/extra_supplies.csv",
        cypher='''
            UNWIND $rows AS row
            MATCH (s:Firm {firm_id: row.source_id})
            MATCH (t:Firm {firm_id: row.target_id})
            MERGE (s)-[r:SUPPLIES {year: row.year}]->(t)
            SET r.amount = row.amount
        ''',
    )
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from nice_poc.etl.sinks import Neo4jSink, PgSink


@dataclass(frozen=True, slots=True)
class UploadReport:
    rows_read: int
    rows_loaded: int
    dry_run: bool


def _read_csv(
    path: str | Path,
    *,
    rename: Mapping[str, str] | None,
    columns: Sequence[str] | None,
    delimiter: str,
    encoding: str,
) -> pd.DataFrame:
    df = pd.read_csv(path, sep=delimiter, encoding=encoding, keep_default_na=True, na_values=[""])
    if rename:
        df = df.rename(columns=dict(rename))
    if columns is not None:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise KeyError(f"CSV missing required columns: {missing}")
        df = df.reindex(columns=list(columns))
    return df


def upload_to_pg(
    csv_path: str | Path,
    *,
    table: str,
    pk: Sequence[str],
    columns: Sequence[str] | None = None,
    rename: Mapping[str, str] | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8",
    dry_run: bool = False,
    sink: PgSink | None = None,
) -> UploadReport:
    df = _read_csv(csv_path, rename=rename, columns=columns,
                   delimiter=delimiter, encoding=encoding)
    rows_read = len(df)
    if dry_run:
        return UploadReport(rows_read=rows_read, rows_loaded=0, dry_run=True)
    sink = sink or PgSink()
    loaded = sink.upsert(table, df, pk=list(pk), columns=columns)
    return UploadReport(rows_read=rows_read, rows_loaded=loaded, dry_run=False)


def upload_to_neo4j(
    csv_path: str | Path,
    *,
    cypher: str,
    rename: Mapping[str, str] | None = None,
    columns: Sequence[str] | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8",
    dry_run: bool = False,
    sink: Neo4jSink | None = None,
) -> UploadReport:
    df = _read_csv(csv_path, rename=rename, columns=columns,
                   delimiter=delimiter, encoding=encoding)
    rows_read = len(df)
    if dry_run:
        return UploadReport(rows_read=rows_read, rows_loaded=0, dry_run=True)
    sink = sink or Neo4jSink()
    loaded = sink.run_unwind(cypher, df)
    return UploadReport(rows_read=rows_read, rows_loaded=loaded, dry_run=False)


__all__ = ["UploadReport", "upload_to_pg", "upload_to_neo4j"]
