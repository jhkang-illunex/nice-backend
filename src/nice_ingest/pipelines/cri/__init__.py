"""공급망 CRI 산출 파이프라인 (CSV 입출력, DB 미사용).

본 패키지의 import 만으로 ``nice_ingest.registry`` 에 자동 등록(autoload).
"""

from __future__ import annotations

from nice_ingest.pipelines.cri.pipeline import add_args, run
from nice_ingest.registry import Pipeline, register

register(
    Pipeline(
        name="cri",
        description="회사·거래내역 CSV → 누적 판매/구매망 CRI 등급·점수 CSV (DB 미사용)",
        add_args=add_args,
        run=run,
    )
)
