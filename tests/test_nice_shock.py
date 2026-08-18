"""nice_shock — 순수 전파 API/시나리오 테스트 (DB 의존 없음)."""
from __future__ import annotations

import subprocess
import sys

from fastapi.testclient import TestClient

from nice_shock.api.main import app
from nice_shock.scenario import run_tariff, run_volume

client = TestClient(app)

# 검증된 예제: 포스코→지오(1.0), 지오→삼성(0.4755), 현대모비스→삼성(0.5245).
# 삼성 인입 비중 합=1.0, 두 경로가 모두 시드(-0.2 고정)로 추적 → 삼성=-0.2.
_TRIPLES = [
    {"from": "포스코", "to": "지오", "rate": 1.0},
    {"from": "지오", "to": "삼성", "rate": 0.4755},
    {"from": "현대모비스", "to": "삼성", "rate": 0.5245},
]


def test_nice_shock_is_db_free() -> None:
    """nice_shock import 가 DB 스택(nice_poc/sqlalchemy)을 끌어오지 않아야 한다.

    전역 sys.modules 는 다른 테스트가 오염시키므로, **깨끗한 서브프로세스**에서 검사.
    """
    code = (
        "import sys, nice_shock.api.main, nice_shock.scenario;"
        "leaked=[m for m in sys.modules if m.startswith('nice_poc') or m=='sqlalchemy'];"
        "print(leaked); sys.exit(1 if leaked else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"shock 서버가 DB 의존을 끌어옴: {r.stdout.strip()}"


def test_tariff_pinned_convex_combination() -> None:
    """가중평균(비중 합 1.0)·시드 고정 → 삼성 = 주입 충격금액 그대로.

    shock_amount 는 원 단위 충격금액 — 전파가 선형이라 검증 값 자체는 단위 무관.
    """
    res = run_tariff(
        _TRIPLES,
        [
            {"node_id": "포스코", "shock_amount": -0.2},
            {"node_id": "현대모비스", "shock_amount": -0.2},
        ],
        [0],
        pin_seeds=True,
    )
    sm = {r["bizno"]: r["shock"] for r in res[0]["result"].shock_list}
    assert sm["삼성"] == -0.2
    assert sm["포스코"] == -0.2 and sm["현대모비스"] == -0.2  # pin 고정
    assert res[0]["result"].converged is True
    assert res[0]["excluded"] == []


def _tariff_body(seed_ids: list[str], shock_rate: float = 0.2) -> dict:
    """공통 tariff 요청 — total_amount=1.0 · rate 모킹 1.0 → 주입액 = shock_rate 그대로.

    upche_cd 는 "<seed_id>_up" — rate 조회 키가 seed_id 가 아니라 upche_cd 임을 테스트에서
    구분하기 위한 규약.
    """
    return {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "shock_rate": shock_rate,
        "seed_list": [
            {"seed_id": s, "upche_cd": f"{s}_up", "total_amount": 1.0, "hscodes": ["390110"]}
            for s in seed_ids
        ],
        "direction": "export",  # 매출(downstream) — 엣지 그대로
    }


def test_tariff_api_endpoint(monkeypatch) -> None:
    # 입력: shock_rate(영향 비중, 0~1)+seed_list[{seed_id,total_amount,hscode}].
    # rate(HS 수출입 비중)는 backend API 조회 — 테스트에선 1.0 모킹.
    # 주입 = total_amount × rate × shock_rate.
    import nice_shock.api.main as m

    monkeypatch.setattr(m, "fetch_rate", lambda seed_id, hscode: 1.0)
    r = client.post("/api/shock/tariff", json=_tariff_body(["포스코", "현대모비스"]))
    assert r.status_code == 200
    d = r.json()
    assert d["direction"] == "export"  # 입력 echo
    assert "iterations" not in d and "converged" not in d  # 간소화로 제거됨
    assert d["excluded_seeds"] == []
    rows = d["data_list"]
    sm = {x["node_id"]: x["shock"] for x in rows}
    assert abs(sm["삼성"] - 0.2) < 1e-9
    dep = {x["node_id"]: x["depth"] for x in rows}  # depth: 시드=1, 홉당 +1
    assert dep["포스코"] == 1 and dep["현대모비스"] == 1
    assert dep["삼성"] == 2 and dep["지오"] == 2


def test_tariff_multi_hscode_rates_summed(monkeypatch) -> None:
    """hscodes 전량 조회·단순 합산 — total 100만 × (0.3+0.2) × shock_rate 0.2 = 10만.

    이중계상(중복/prefix) 제거 정책은 발주처 회신 대기 — 현재 계약은 Σ 그대로.
    """
    import nice_shock.api.main as m

    rates = {"390110": 0.3, "840999": 0.2}
    monkeypatch.setattr(m, "fetch_rate", lambda upche_cd, hscode: rates[hscode])
    body = _tariff_body(["포스코"])
    body["seed_list"][0]["total_amount"] = 1_000_000.0
    body["seed_list"][0]["hscodes"] = ["390110", "840999"]
    d = client.post("/api/shock/tariff", json=body).json()
    sm = {x["node_id"]: x["shock"] for x in d["data_list"]}
    assert abs(sm["포스코"] - 100_000.0) < 1e-6
    assert abs(sm["지오"] - 100_000.0) < 1e-6  # 포스코→지오 rate 1.0 전파


def test_tariff_partial_hscode_failure_partial_sum(monkeypatch) -> None:
    """일부 hscode 조회 실패(404 등)면 그 코드만 빼고 부분 합산 — 시드는 excluded 아님.

    전 코드 실패 시에만 시드 excluded (test_tariff_rate_lookup_failure_excluded).
    """
    from nice_shock.rate_client import RateLookupFailed

    import nice_shock.api.main as m

    def fake_rate(upche_cd: str, hscode: str) -> float:
        if hscode == "0000999999":
            raise RateLookupFailed(f"(upche_cd={upche_cd}, hscode={hscode}) 거래 없음 (404)")
        return 0.5

    monkeypatch.setattr(m, "fetch_rate", fake_rate)
    body = _tariff_body(["포스코"])
    body["seed_list"][0]["total_amount"] = 1_000_000.0
    body["seed_list"][0]["hscodes"] = ["390110", "0000999999"]
    d = client.post("/api/shock/tariff", json=body).json()
    assert d["excluded_seeds"] == []  # 부분 실패는 제외 아님
    sm = {x["node_id"]: x["shock"] for x in d["data_list"]}
    assert abs(sm["포스코"] - 100_000.0) < 1e-6  # 1e6 × 0.5(성공분만) × 0.2


def test_tariff_default_direction_is_import(monkeypatch) -> None:
    """direction 미입력 시 import(매입) 기본."""
    import nice_shock.api.main as m

    monkeypatch.setattr(m, "fetch_rate", lambda seed_id, hscode: 1.0)
    body = _tariff_body(["포스코"])
    del body["direction"]
    d = client.post("/api/shock/tariff", json=body).json()
    assert d["direction"] == "import"


def test_shock_rate_forced_0_to_1(monkeypatch) -> None:
    """shock_rate(영향 비중)는 0~1 강제 — 범위 밖(음수·1 초과)은 422."""
    import nice_shock.api.main as m

    monkeypatch.setattr(m, "fetch_rate", lambda seed_id, hscode: 1.0)
    assert client.post("/api/shock/tariff", json=_tariff_body(["포스코"], shock_rate=-0.2)).status_code == 422
    assert client.post("/api/shock/tariff", json=_tariff_body(["포스코"], shock_rate=1.5)).status_code == 422
    vol = {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "seed_list": [{"seed_id": "포스코", "total_amount": 1.0, "shock_rate": -0.2}],
        "direction": "export",
    }
    assert client.post("/api/shock/volume", json=vol).status_code == 422


def test_tariff_input_schema() -> None:
    """tariff=shock_rate+seed_list[{seed_id,total_amount,hscode}] / volume=seed_list[{seed_id,total_amount,shock_rate}]."""
    from nice_shock.api.main import (
        ExcludedSeedOut,
        TariffRequest,
        TariffSeedIn,
        VolumeRequest,
        VolumeSeedIn,
    )

    assert set(TariffRequest.model_fields) == {
        "triple_list", "shock_rate", "seed_list", "direction",
    }
    assert set(TariffSeedIn.model_fields) == {"seed_id", "upche_cd", "total_amount", "hscodes"}
    assert set(VolumeRequest.model_fields) == {"triple_list", "seed_list", "direction"}
    assert set(VolumeSeedIn.model_fields) == {"seed_id", "total_amount", "shock_rate"}
    assert set(ExcludedSeedOut.model_fields) == {"node_id", "reason"}


def test_tariff_isolated_seed_excluded(monkeypatch) -> None:
    """edgelist(from∪to)에 없는 시드는 전파 제외 + excluded_seeds(사유 포함) 보고.

    제외 시드는 data_list·total_shock 에 포함되지 않는다 (조용한 echo 금지 —
    시드/그래프 조립 불일치를 클라이언트가 즉시 인지).
    """
    import nice_shock.api.main as m

    monkeypatch.setattr(m, "fetch_rate", lambda seed_id, hscode: 1.0)
    d = client.post("/api/shock/tariff", json=_tariff_body(["포스코", "유령기업"])).json()
    assert [e["node_id"] for e in d["excluded_seeds"]] == ["유령기업"]
    assert "노드 집합" in d["excluded_seeds"][0]["reason"]
    ids = {x["node_id"] for x in d["data_list"]}
    assert "유령기업" not in ids
    # total_shock 에 고립 시드의 주입액이 합산되지 않음 (포스코 경로 합만)
    assert abs(d["total_shock"] - sum(x["shock"] for x in d["data_list"])) < 1e-9


def test_tariff_rate_lookup_failure_excluded(monkeypatch) -> None:
    """rate 조회 실패(404·형식·0~1 범위 위반) 시드는 excluded_seeds 로 보고, 나머지는 정상 전파.

    조회 키가 upche_cd 임을 함께 검증 — mock 이 upche_cd("현대모비스_up") 로 분기.
    """
    from nice_shock.rate_client import RateLookupFailed

    import nice_shock.api.main as m

    def fake_rate(upche_cd: str, hscode: str) -> float:
        if upche_cd == "현대모비스_up":
            raise RateLookupFailed(f"(upche_cd={upche_cd}, hscode={hscode}) 거래 없음 (404)")
        return 1.0

    monkeypatch.setattr(m, "fetch_rate", fake_rate)
    d = client.post("/api/shock/tariff", json=_tariff_body(["포스코", "현대모비스"])).json()
    assert [e["node_id"] for e in d["excluded_seeds"]] == ["현대모비스"]
    assert "거래 없음" in d["excluded_seeds"][0]["reason"]
    sm = {x["node_id"]: x["shock"] for x in d["data_list"]}
    assert "현대모비스" not in sm and "포스코" in sm  # 포스코 경로만 전파


def test_tariff_rate_api_unconfigured_503(monkeypatch) -> None:
    """RATE_API_URL 미설정이면 시드 단위가 아니라 요청 전체 503 (서비스 구성 문제)."""
    monkeypatch.delenv("RATE_API_URL", raising=False)
    r = client.post("/api/shock/tariff", json=_tariff_body(["포스코"]))
    assert r.status_code == 503
    assert "RATE_API_URL" in r.json()["detail"]


def test_rate_client_validates_range(monkeypatch) -> None:
    """backend 응답 rate 가 0~1 밖이면 RateLookupFailed (강제 검증)."""
    import pytest

    import nice_shock.rate_client as rc

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"rate": 1.5}

    monkeypatch.setenv("RATE_API_URL", "http://rate-api.test/rate")
    monkeypatch.setattr(rc.httpx, "get", lambda *a, **k: _Resp())
    with pytest.raises(rc.RateLookupFailed, match="범위 위반"):
        rc.fetch_rate("포스코_up", "390110")


# ── rate-mock (backend rate API 목업) ─────────────────────────────────────────


def test_rate_mock_deterministic_and_bounded() -> None:
    """목업 rate — 같은 (upche_cd, hscode) 는 항상 같은 값, 0~1 범위."""
    from nice_shock.mock_rate_api import app as mock_app

    mc = TestClient(mock_app)
    r1 = mc.get("/rate", params={"upche_cd": "184084", "hscode": "3801300000"})
    r2 = mc.get("/rate", params={"upche_cd": "184084", "hscode": "3801300000"})
    assert r1.status_code == 200
    assert r1.json() == r2.json()  # 결정적
    assert 0.0 <= r1.json()["rate"] <= 1.0
    # 다른 키 → (거의 확실히) 다른 값
    r3 = mc.get("/rate", params={"upche_cd": "184084", "hscode": "390110"})
    assert r3.json()["rate"] != r1.json()["rate"]


def test_rate_mock_404_convention() -> None:
    """목업 규약 — hscode '0000' 접두는 거래 없음(404)."""
    from nice_shock.mock_rate_api import app as mock_app

    mc = TestClient(mock_app)
    assert mc.get("/rate", params={"upche_cd": "184084", "hscode": "0000999999"}).status_code == 404
    assert mc.get("/health").json()["status"] == "ok"


def test_tariff_end_to_end_with_rate_mock(monkeypatch) -> None:
    """shock-server rate_client → rate-mock 실호출 경로 검증 (httpx.get 을 목업 앱으로 우회)."""
    import nice_shock.rate_client as rc
    from nice_shock.mock_rate_api import app as mock_app

    mc = TestClient(mock_app)
    monkeypatch.setenv("RATE_API_URL", "http://rate-mock.test/rate")
    monkeypatch.setattr(rc.httpx, "get", lambda url, params, timeout: mc.get("/rate", params=params))
    body = _tariff_body(["포스코"])
    d = client.post("/api/shock/tariff", json=body)
    assert d.status_code == 200
    sm = {x["node_id"]: x["shock"] for x in d.json()["data_list"]}
    # 목업 rate(0.05~0.95) × total 1.0 × shock_rate 0.2 — 값 자체보다 경로·범위 검증
    assert 0.0 < sm["포스코"] <= 0.2 * 0.95 * 1.0001


def test_volume_amount_zero_based() -> None:
    """거래량 변동 — 시드 shock_amount=−200만(변동금액, 원) → 결과=변동금액.

    입·출력 모두 0-기준 금액(0=무변화, tariff 와 통일). 과거 node_overrides[{p1,delta}]
    (변동율 입력) 폐기.
    """
    res = run_volume(
        _TRIPLES, [{"node_id": "포스코", "shock_amount": -2_000_000.0}], [0], pin_seeds=False
    )
    sm = {r["bizno"]: r["shock"] for r in res[0]["result"].shock_list}
    # 포스코 −200만 → 변동금액 −200만. 지오는 포스코×1.0 전파 → −200만.
    assert abs(sm["포스코"] - (-2_000_000.0)) < 1e-6
    assert abs(sm["지오"] - (-2_000_000.0)) < 1e-6


def test_volume_api_amount_times_rate() -> None:
    """공개 /api/shock/volume — 시드별 total_amount×shock_rate 주입, 출력=변동금액(원).

    포스코 1천만원 × 0.2 = 200만원 주입 → 지오는 rate 1.0 전파로 200만원.
    """
    body = {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "seed_list": [
            {"seed_id": "포스코", "total_amount": 10_000_000.0, "shock_rate": 0.2}
        ],
        "direction": "export",
    }
    d = client.post("/api/shock/volume", json=body).json()
    sm = {x["node_id"]: x["shock"] for x in d["data_list"]}
    assert abs(sm["포스코"] - 2_000_000.0) < 1e-6  # 0-기준 금액: 무변화=0
    assert abs(sm["지오"] - 2_000_000.0) < 1e-6


def test_volume_api_isolated_seed_excluded() -> None:
    """volume 도 tariff 와 동일한 필터링 — 그래프 밖 시드는 excluded_seeds(사유 포함)로."""
    body = {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "seed_list": [
            {"seed_id": "포스코", "total_amount": 1_000_000.0, "shock_rate": 0.2},
            {"seed_id": "유령기업", "total_amount": 1_000_000.0, "shock_rate": 0.5},
        ],
        "direction": "export",
    }
    d = client.post("/api/shock/volume", json=body).json()
    assert [e["node_id"] for e in d["excluded_seeds"]] == ["유령기업"]
    assert "유령기업" not in {x["node_id"] for x in d["data_list"]}


def test_propagate_endpoint_edges_init() -> None:
    """저수준 /api/shock/propagate — 이미 정향된 edges + 노드별 init 직접 전파."""
    r = client.post(
        "/api/shock/propagate",
        json={"triple_list": [{"from": "A", "to": "B", "rate": 0.5}], "init": {"A": 1.0}},
    )
    assert r.status_code == 200
    d = r.json()
    sm = {x["bizno"]: x["shock"] for x in d["shock_list"]}
    assert sm["A"] == 1.0 and abs(sm["B"] - 0.5) < 1e-9  # B = A×0.5


# ── CRI(신용위험지표) ──────────────────────────────────────────────────────────
_CRI_NODES = [
    {"id": "A", "grade": "AA", "sales": 1000},
    {"id": "B", "grade": "NR", "sales": 800},   # 무등급 — 전파 포함·CRI 제외
    {"id": "C", "grade": "BBB", "sales": 500},
    {"id": "D", "grade": "A", "sales": 600},
    {"id": "E", "grade": "BB", "sales": 400},
]
_CRI_EDGES = [
    {"source": "A", "target": "B", "sell_share": 0.300, "buy_share": 0.375},
    {"source": "A", "target": "D", "sell_share": 0.200, "buy_share": 0.333},
    {"source": "D", "target": "B", "sell_share": 0.300, "buy_share": 0.225},
    {"source": "D", "target": "E", "sell_share": 0.400, "buy_share": 0.600},
    {"source": "B", "target": "C", "sell_share": 0.500, "buy_share": 0.800},
    {"source": "B", "target": "A", "sell_share": 0.200, "buy_share": 0.160},
]


def test_cri_matches_spec() -> None:
    """엔진이 스펙 샘플 출력과 일치 — A 판매망·네트워크 지수."""
    from nice_shock.cri import compute_cri

    r = compute_cri(_CRI_NODES, _CRI_EDGES)
    a = r["nodes"]["A"]["sell"]
    assert abs(a["total_weight"] - 0.883621) < 1e-5   # 간접·loop 누적
    assert abs(a["valid_weight"] - 0.495690) < 1e-5   # B(NR) 제외
    assert abs(a["coverage"] - 0.560976) < 1e-5
    assert abs(a["avg_cri"] - 3.739130) < 1e-5
    assert abs(a["exposure"] - 1.853448) < 1e-5
    # 판매 엣지 없는 C 는 판매망 지표 None
    assert r["nodes"]["C"]["sell"]["coverage"] is None
    assert abs(r["network"]["sell"]["risk_index"] - 3.784242) < 1e-5
    assert abs(r["network"]["buy"]["risk_index"] - 2.393419) < 1e-5


def test_cri_endpoint() -> None:
    """공개 /api/cri (shock 와 동급) — 노드별 sell/buy 속성 + 네트워크 지표."""
    r = client.post("/api/cri", json={"nodes": _CRI_NODES, "edges": _CRI_EDGES})
    assert r.status_code == 200
    d = r.json()
    nodes = {n["id"]: n for n in d["data_list"]}
    assert abs(nodes["A"]["sell"]["avg_cri"] - 3.739130) < 1e-5
    assert nodes["C"]["sell"]["coverage"] is None  # 판매 엣지 없음
    assert abs(d["network"]["buy"]["risk_index"] - 2.393419) < 1e-5


def test_cri_is_db_free() -> None:
    """nice_shock.cri 도 DB 스택을 끌어오지 않아야 한다(서브프로세스)."""
    code = (
        "import sys, nice_shock.cri;"
        "leaked=[m for m in sys.modules if m.startswith('nice_poc') or m=='sqlalchemy'];"
        "print(leaked); sys.exit(1 if leaked else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, f"cri 가 DB 의존을 끌어옴: {r.stdout.strip()}"


def test_health() -> None:
    assert client.get("/health").json()["status"] == "ok"
