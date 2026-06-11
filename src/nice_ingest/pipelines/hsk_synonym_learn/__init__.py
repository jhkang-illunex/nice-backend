"""동의어 self-play 학습 파이프라인.

본 패키지의 import 만으로 ``nice_ingest.registry`` 에 자동 등록(autoload).
"""

from __future__ import annotations

from nice_ingest.pipelines.hsk_synonym_learn.pipeline import add_args, run
from nice_ingest.registry import Pipeline, register

register(
    Pipeline(
        name="hsk_synonym_learn",
        description="저신뢰 질의(search_log) → LLM 후보 생성 → 검색 점수 self-play 검증 → synonyms 자동 등록",
        add_args=add_args,
        run=run,
    )
)
