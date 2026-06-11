"""HSK 검색 텍스트 보강 파이프라인 (detail chain + search_text + tsv).

본 패키지의 import 만으로 ``nice_ingest.registry`` 에 자동 등록(autoload).
"""

from __future__ import annotations

from nice_ingest.pipelines.hsk_enrich.pipeline import add_args, run
from nice_ingest.registry import Pipeline, register

register(
    Pipeline(
        name="hsk_enrich",
        description="s_ra417 계층 chain → hsk.detail_ko/en + search_text(7-슬롯) + search_tsv 재생성",
        add_args=add_args,
        run=run,
    )
)
