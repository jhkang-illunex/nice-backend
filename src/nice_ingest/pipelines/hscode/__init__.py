"""HSCode 파이프라인 — 관세청 HS부호 Excel → PostgreSQL ``hsk`` 테이블."""

from __future__ import annotations

from nice_ingest.pipelines.hscode.pipeline import add_args, run
from nice_ingest.registry import Pipeline, register

register(
    Pipeline(
        name="hscode",
        description="관세청 HS부호 xlsx → pg.hsk (1차 적재; 색인/임베딩은 별 단계)",
        add_args=add_args,
        run=run,
    )
)
