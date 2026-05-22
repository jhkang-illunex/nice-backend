"""화면 ①~⑦ + 검색 라우터 등록 및 stub 동작 검증.

데이터 적재 전이라 응답은 501 이지만 OpenAPI contract 는 프론트엔드와 합의된 형태로
완전히 노출되어야 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from nice_poc.api.main import app
from nice_poc.api.schemas import (
    FirmImpact,
    KpiCard,
    Paginated,
)

EXPECTED_PATHS = {
    "/health",
    "/health/deep",
    "/api/run/{run_id}/kpi",
    "/api/scenario",
    "/api/run",
    "/api/run/{run_id}/firms",
    "/api/run/{run_id}/network",
    "/api/run/{run_id}/firm/{firm_id}",
    "/api/run/{run_id}/firm/{firm_id}/path",
    "/api/run/{run_id}/by-sector",
    "/api/group/{group_id}/timeseries",
    "/api/search/autocomplete",
    "/api/search/semantic",
}


def test_openapi_registers_all_paths() -> None:
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    registered = set(spec["paths"].keys())
    missing = EXPECTED_PATHS - registered
    assert not missing, f"missing paths in OpenAPI: {missing}"


def test_openapi_has_at_least_11_business_paths() -> None:
    client = TestClient(app)
    spec = client.get("/openapi.json").json()
    business_paths = [p for p in spec["paths"] if p.startswith("/api/")]
    assert len(business_paths) >= 11, business_paths


def test_kpi_stub_returns_501() -> None:
    client = TestClient(app)
    r = client.get("/api/run/RUN_X/kpi")
    assert r.status_code == 501
    assert r.json()["detail"] == "data not loaded yet"


def test_autocomplete_stub_returns_501() -> None:
    client = TestClient(app)
    r = client.get("/api/search/autocomplete", params={"q": "삼성"})
    assert r.status_code == 501


def test_schemas_instantiate() -> None:
    kpi = KpiCard(
        revenue_total=1.0,
        cost_total=0.5,
        profit_total=0.5,
        capped_ratio=0.1,
        firm_count=10,
        top_severity=0.3,
    )
    assert kpi.firm_count == 10

    impact = FirmImpact(
        firm_id="F1",
        firm_name="테스트",
        sector_code="C26",
        revenue_initial=1.0,
        revenue_propagation=0.5,
        revenue_sum=1.5,
        cost_initial=0.2,
        cost_propagation=0.1,
        cost_sum=0.3,
        profit_initial=0.8,
        profit_propagation=0.4,
        profit_sum=1.2,
        impact_score=1.2,
        capped=False,
    )
    page: Paginated[FirmImpact] = Paginated[FirmImpact](items=[impact], page=1, size=100, total=1)
    assert page.items[0].firm_id == "F1"
    # generic 도 datetime 도 정상인지 확인
    assert isinstance(datetime.now(UTC), datetime)
