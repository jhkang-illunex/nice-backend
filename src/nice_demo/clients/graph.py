"""graph-analysis REST 클라이언트 — network 분석 API thin wrapper.

데모에서는 *전체 그래프 분석* 보다는 *시드 기반 부분 확장* 위주.
시드+확장은 MVP 에서 ``pipeline/subgraph.py`` 가 직접 PG SQL 로 처리하므로
여기서는 summary / neighbors 정도만 노출한다 (future use).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from nice_demo.config import get_demo_settings


@dataclass(frozen=True)
class GraphClient:
    base_url: str
    timeout_s: float

    def summary(self, *, trade_year: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if trade_year:
            params["trade_year"] = trade_year
        with httpx.Client(timeout=self.timeout_s) as cx:
            r = cx.get(
                f"{self.base_url.rstrip('/')}/api/network/summary", params=params
            )
            r.raise_for_status()
            return r.json()

    def neighbors(
        self,
        bizno: str,
        *,
        depth: int = 1,
        direction: str = "both",
        trade_year: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"depth": depth, "direction": direction}
        if trade_year:
            params["trade_year"] = trade_year
        with httpx.Client(timeout=self.timeout_s) as cx:
            r = cx.get(
                f"{self.base_url.rstrip('/')}/api/network/neighbors/{bizno}",
                params=params,
            )
            r.raise_for_status()
            return r.json()


@lru_cache
def get_graph_client() -> GraphClient:
    s = get_demo_settings()
    return GraphClient(base_url=s.graph_api_url, timeout_s=s.rest_timeout_s)
