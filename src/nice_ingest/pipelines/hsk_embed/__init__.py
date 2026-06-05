"""HSK 임베딩 일괄 적재 파이프라인.

본 패키지의 import 만으로 ``nice_ingest.registry`` 에 자동 등록(autoload).
"""

from __future__ import annotations

from nice_ingest.pipelines.hsk_embed.pipeline import add_args, run
from nice_ingest.registry import Pipeline, register

register(
    Pipeline(
        name="hsk_embed",
        description="hsk.search_text → Qwen3-Embedding-0.6B → hsk.embedding (UPDATE)",
        add_args=add_args,
        run=run,
    )
)
