"""KSIC 임베딩 헬퍼 — ``rag.ksic.search_text`` 일괄 임베딩.

모델·전처리 정책(BGE-M3 raw text, L2 정규화, Matryoshka truncate)은
``hsk_embed`` 와 완전히 동일 — 문서 임베딩 함수(:func:`embed_documents`) 와
리포트 타입을 그대로 재사용하고, 대상 테이블/키만 다르다.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from nice_common.db import get_pg_engine
from nice_rag.search.hsk_embed import (
    BulkEmbedReport,
    _vec_to_pg,
    embed_documents,
)

log = logging.getLogger(__name__)

_SELECT_CANDIDATES = text(
    """
    SELECT code, search_text
    FROM rag.ksic
    WHERE search_text IS NOT NULL
      AND (:only_missing = false OR embedding IS NULL)
    ORDER BY code
    """
)

_UPDATE_EMBEDDING = text(
    "UPDATE rag.ksic SET embedding = CAST(:embedding AS vector) WHERE code = :code"
)


def bulk_embed_ksic(
    *,
    batch_size: int = 64,
    only_missing: bool = True,
    dry_run: bool = False,
    limit: int | None = None,
) -> BulkEmbedReport:
    """``ksic.search_text`` 일괄 임베딩 → ``ksic.embedding`` UPDATE.

    98 row 뿐이라 배치 1~2회로 끝난다. 재적재로 search_text 가 바뀌었으면
    ``only_missing=False`` (CLI ``--rebuild``) 로 재임베딩할 것.
    """
    report = BulkEmbedReport(dry_run=dry_run)
    engine = get_pg_engine()

    with engine.connect() as conn:
        rows = conn.execute(
            _SELECT_CANDIDATES, {"only_missing": only_missing}
        ).mappings().all()

    if limit is not None:
        rows = rows[:limit]
    report.candidates = len(rows)
    if not rows:
        return report

    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        vecs = embed_documents([r["search_text"] for r in chunk])
        if len(vecs) != len(chunk):
            raise RuntimeError(
                f"embed backend returned {len(vecs)} vectors for {len(chunk)} inputs"
            )

        report.batches += 1
        if dry_run:
            report.embedded += len(chunk)
            continue

        update_params = [
            {"code": r["code"], "embedding": _vec_to_pg(v)}
            for r, v in zip(chunk, vecs, strict=True)
        ]
        with engine.begin() as conn:
            conn.execute(_UPDATE_EMBEDDING, update_params)
        report.embedded += len(chunk)
        log.info(
            "embedded batch %d (%d/%d)", report.batches, report.embedded, report.candidates
        )

    return report
