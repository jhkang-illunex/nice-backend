"""KSIC 파이프라인 — 한국표준산업분류(제11차) Excel → PostgreSQL ``rag.ksic``."""

from __future__ import annotations

from nice_ingest.pipelines.ksic.pipeline import add_args, run
from nice_ingest.registry import Pipeline, register

register(
    Pipeline(
        name="ksic",
        description="KSIC 제11차 분류체계 xlsx → rag.ksic 대·중분류 98 row (임베딩은 ksic_embed)",
        add_args=add_args,
        run=run,
    )
)
