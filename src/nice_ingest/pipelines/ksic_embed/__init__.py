"""KSIC 임베딩 파이프라인 — ``rag.ksic.search_text`` → ``embedding`` 일괄 적재."""

from __future__ import annotations

from nice_ingest.pipelines.ksic_embed.pipeline import add_args, run
from nice_ingest.registry import Pipeline, register

register(
    Pipeline(
        name="ksic_embed",
        description="rag.ksic.search_text 일괄 임베딩 → embedding UPDATE (bge-m3)",
        add_args=add_args,
        run=run,
    )
)
