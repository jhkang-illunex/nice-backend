"""검색 질의 로깅 — 자기보완 루프의 입력 (저신뢰 질의 큐).

best-effort: 로깅 실패가 검색 응답을 깨뜨리면 안 되므로 모든 예외를 삼킨다.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from nice_common.db import get_pg_engine
from nice_rag.config import get_rag_settings
from nice_rag.search.hsk_index import HybridHit

log = logging.getLogger(__name__)

_INSERT = text(
    """
    INSERT INTO rag.search_log (query, query_expanded, top_score, top_codes, low_confidence)
    VALUES (:q, :qx, :score, :codes, :low)
    """
)


def log_search(query: str, query_expanded: str, hits: list[HybridHit]) -> None:
    try:
        top_score = hits[0].score if hits else None
        low = top_score is None or top_score < get_rag_settings().lowconf_threshold
        with get_pg_engine().begin() as c:
            c.execute(
                _INSERT,
                {
                    "q": query,
                    "qx": query_expanded,
                    "score": top_score,
                    "codes": [h.hs_code for h in hits[:5]],
                    "low": low,
                },
            )
    except Exception:  # noqa: BLE001 — 로깅은 검색을 깨뜨리지 않는다
        log.warning("search_log insert failed", exc_info=True)
