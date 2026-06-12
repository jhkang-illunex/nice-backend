"""관세청 공식 단위별 품목명 적재 파이프라인 (rag.hs_heading).

본 패키지의 import 만으로 ``nice_ingest.registry`` 에 자동 등록(autoload).
"""

from __future__ import annotations

from nice_ingest.pipelines.hs_heading.pipeline import add_args, run
from nice_ingest.registry import Pipeline, register

register(
    Pipeline(
        name="hs_heading",
        description="관세청 단위별 품목명 xlsx → rag.hs_heading (공식 호·소호 명칭)",
        add_args=add_args,
        run=run,
    )
)
