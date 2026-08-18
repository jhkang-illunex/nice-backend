"""통합 검색(/api/search) 라우터 단위 테스트 — 외부 백엔드 의존 없음.

hsk/ksic 라우터의 search 함수를 monkeypatch 해 병합·부분 실패·병렬 실행을
검증한다 (임베딩/PG 미접속).
"""

from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from nice_rag.api.main import app
from nice_rag.api.routers import hsk as hsk_router
from nice_rag.api.routers import ksic as ksic_router
from nice_rag.api.routers.hsk import HskHit
from nice_rag.api.routers.ksic import KsicHit

client = TestClient(app)

_HSK_HIT = HskHit(hs_code="8541400000", name_ko="반도체 디바이스", name_en=None,
                  description=None, score=0.0492)
_KSIC_HIT = KsicHit(code="26", level=2, parent_code="C",
                    name_ko="전자 부품, 컴퓨터, 영상, 음향 및 통신장비 제조업",
                    division_range=None, score=0.0444)


def test_unified_merges_both(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hsk_router, "search", lambda **kw: [_HSK_HIT])
    monkeypatch.setattr(ksic_router, "search", lambda **kw: [_KSIC_HIT])

    r = client.get("/api/search", params={"q": "반도체"})
    assert r.status_code == 200
    body = r.json()
    assert body["hsk"][0]["hs_code"] == "8541400000"
    assert body["ksic"][0]["code"] == "26"
    assert body["errors"] == {}


def test_unified_passes_params_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, dict] = {}
    monkeypatch.setattr(hsk_router, "search", lambda **kw: seen.setdefault("hsk", kw) and [])
    monkeypatch.setattr(ksic_router, "search", lambda **kw: seen.setdefault("ksic", kw) and [])

    r = client.get(
        "/api/search",
        params={"q": "반도체", "limit": 7, "hs_prefix": "85", "active_only": "true", "level": 2},
    )
    assert r.status_code == 200
    assert seen["hsk"] == {"q": "반도체", "limit": 7, "hs_prefix": "85", "active_only": True}
    assert seen["ksic"] == {"q": "반도체", "limit": 7, "level": 2}


def test_unified_partial_failure_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kw):
        raise HTTPException(status_code=503, detail="embed backend unreachable (test)")

    monkeypatch.setattr(hsk_router, "search", _boom)
    monkeypatch.setattr(ksic_router, "search", lambda **kw: [_KSIC_HIT])

    r = client.get("/api/search", params={"q": "반도체"})
    assert r.status_code == 200
    body = r.json()
    assert body["hsk"] == []
    assert body["ksic"][0]["code"] == "26"
    assert "hsk" in body["errors"] and "embed backend unreachable" in body["errors"]["hsk"]


def test_unified_total_failure_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**kw):
        raise HTTPException(status_code=503, detail="down")

    monkeypatch.setattr(hsk_router, "search", _boom)
    monkeypatch.setattr(ksic_router, "search", _boom)

    r = client.get("/api/search", params={"q": "반도체"})
    assert r.status_code == 503
    assert "all backends failed" in r.json()["detail"]


def test_unified_runs_domains_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    """두 도메인이 순차가 아닌 병렬로 실행되는지 — 서로가 시작될 때까지 대기.

    순차 실행이면 첫 함수가 barrier 에서 타임아웃 → BrokenBarrierError 로 실패.
    """
    barrier = threading.Barrier(2, timeout=5)

    def _hsk(**kw):
        barrier.wait()
        return [_HSK_HIT]

    def _ksic(**kw):
        barrier.wait()
        return [_KSIC_HIT]

    monkeypatch.setattr(hsk_router, "search", _hsk)
    monkeypatch.setattr(ksic_router, "search", _ksic)

    r = client.get("/api/search", params={"q": "반도체"})
    assert r.status_code == 200
    assert r.json()["errors"] == {}
