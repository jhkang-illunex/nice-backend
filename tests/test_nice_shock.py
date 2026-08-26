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


_HS = "3901100000"  # 테스트 공통 HS10


def _tariff_body(seed_ids: list[str], shock_rate: float = 0.2) -> dict:
    """공통 tariff 요청 — total_amount=1.0 · rate 모킹 1.0 → 주입액 = shock_rate 그대로.

    upche_cd 는 "<seed_id>_up" — rate 조회 키가 seed_id 가 아니라 upche_cd 임을 테스트에서
    구분하기 위한 규약.
    """
    return {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "shock_rate": shock_rate,
        "seed_list": [
            {"seed_id": s, "upche_cd": f"{s}_up", "total_amount": 1.0, "hscodes": [_HS]}
            for s in seed_ids
        ],
        "direction": "export",  # 매출(downstream) — 엣지 그대로
    }


def _grid_weights(rate: float = 1.0):
    """fetch_weights 대체 — 요청된 전 (upchecd × hskcode) 셀에 같은 rate."""
    def fake(bse_yr, upchecd_list, hskcode_list, exim):
        return {(u, h): rate for u in upchecd_list for h in hskcode_list}
    return fake


def test_tariff_api_endpoint(monkeypatch) -> None:
    # 입력: shock_rate(충격 비율, 0~1)+seed_list[{seed_id,upche_cd,total_amount,hscodes}].
    # rate(HS10 수출입 비중)는 backend /trade/weight 일괄 조회 — 테스트에선 1.0 모킹.
    # 주입 = total_amount(손익계산서 매출액) × Σrate × shock_rate.
    import nice_shock.api.main as m

    monkeypatch.setattr(m, "fetch_weights", _grid_weights(1.0))
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
    """품목별 비중 합산 — total 100만 × (0.3+0.2) × shock_rate 0.2 = 10만.

    v2 로직: 품목별 금액(매출액×비중)을 더한 뒤 충격 비율을 1회 곱함 —
    선형이라 매출액 × Σ비중 × 충격비율과 동일.
    """
    import nice_shock.api.main as m

    hs2 = "8409990000"
    rates = {_HS: 0.3, hs2: 0.2}

    def fake(bse_yr, upchecd_list, hskcode_list, exim):
        return {(u, h): rates[h] for u in upchecd_list for h in hskcode_list}

    monkeypatch.setattr(m, "fetch_weights", fake)
    body = _tariff_body(["포스코"])
    body["seed_list"][0]["total_amount"] = 1_000_000.0
    body["seed_list"][0]["hscodes"] = [_HS, hs2]
    d = client.post("/api/shock/tariff", json=body).json()
    sm = {x["node_id"]: x["shock"] for x in d["data_list"]}
    assert abs(sm["포스코"] - 100_000.0) < 1e-6
    assert abs(sm["지오"] - 100_000.0) < 1e-6  # 포스코→지오 rate 1.0 전파


def test_tariff_duplicate_hscode_deduped(monkeypatch) -> None:
    """요청에 같은 HS10 이 중복돼도 1회만 합산 — 이중계상 방지."""
    import nice_shock.api.main as m

    monkeypatch.setattr(m, "fetch_weights", _grid_weights(0.5))
    body = _tariff_body(["포스코"])
    body["seed_list"][0]["total_amount"] = 1_000_000.0
    body["seed_list"][0]["hscodes"] = [_HS, _HS]
    d = client.post("/api/shock/tariff", json=body).json()
    sm = {x["node_id"]: x["shock"] for x in d["data_list"]}
    assert abs(sm["포스코"] - 100_000.0) < 1e-6  # 1e6 × 0.5(1회) × 0.2


def test_tariff_missing_hscode_partial_sum(monkeypatch) -> None:
    """일부 품목이 응답에 없으면(실적 없음) 그 코드만 비중 0 취급, 부분 합산 — excluded 아님.

    전 품목 부재 시에만 시드 excluded (test_tariff_no_trade_record_excluded).
    """
    import nice_shock.api.main as m

    missing = "0000999999"

    def fake(bse_yr, upchecd_list, hskcode_list, exim):
        return {(u, h): 0.5 for u in upchecd_list for h in hskcode_list if h != missing}

    monkeypatch.setattr(m, "fetch_weights", fake)
    body = _tariff_body(["포스코"])
    body["seed_list"][0]["total_amount"] = 1_000_000.0
    body["seed_list"][0]["hscodes"] = [_HS, missing]
    d = client.post("/api/shock/tariff", json=body).json()
    assert d["excluded_seeds"] == []  # 부분 부재는 제외 아님
    sm = {x["node_id"]: x["shock"] for x in d["data_list"]}
    assert abs(sm["포스코"] - 100_000.0) < 1e-6  # 1e6 × 0.5(보유분만) × 0.2


def test_tariff_iokind_maps_exim_and_default_year(monkeypatch) -> None:
    """iokind 미입력 시 in(수입) 기본 — rate 조회 tseximdivcd '3'. out=수출 '0'.

    direction 은 전파 방향 전용 — rate 조회 방향(exim)에 영향 없음(2026-08-25 분리).
    bse_yr 미입력 시 기본 2025 로 backend 조회 (v2 §0 기준 연도).
    """
    import nice_shock.api.main as m

    seen: dict = {}

    def fake(bse_yr, upchecd_list, hskcode_list, exim):
        seen.update(bse_yr=bse_yr, exim=exim)
        return {(u, h): 1.0 for u in upchecd_list for h in hskcode_list}

    monkeypatch.setattr(m, "fetch_weights", fake)
    body = _tariff_body(["포스코"])
    del body["direction"]
    d = client.post("/api/shock/tariff", json=body).json()
    assert d["direction"] == "import"
    assert seen == {"bse_yr": "2025", "exim": "3"}  # iokind 기본 in=수입 / 기본연도 2025
    body["direction"] = "export"  # 전파 방향을 바꿔도 exim 은 그대로 (iokind 만 반영)
    body["bse_yr"] = "2023"
    client.post("/api/shock/tariff", json=body)
    assert seen == {"bse_yr": "2023", "exim": "3"}
    body["iokind"] = "out"
    client.post("/api/shock/tariff", json=body)
    assert seen == {"bse_yr": "2023", "exim": "0"}  # out=수출
    body["iokind"] = "수출"  # in/out 외 값은 422
    assert client.post("/api/shock/tariff", json=body).status_code == 422


def test_volume_iokind_reserved_accepted() -> None:
    """volume 의 iokind 는 예약 인자(인자 통일) — in/out 수용·결과 무영향, 그 외 422."""
    vol = {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "seed_list": [{"seed_id": "포스코", "total_amount": 1_000_000.0, "shock_rate": 0.2}],
        "direction": "export",
    }
    base = client.post("/api/shock/volume", json=vol).json()
    for kind in ("in", "out"):
        d = client.post("/api/shock/volume", json={**vol, "iokind": kind}).json()
        assert d == base  # 미사용 — 결과 동일
    assert client.post("/api/shock/volume", json={**vol, "iokind": "x"}).status_code == 422


def test_tariff_hscode_must_be_10_digits() -> None:
    """hscodes 는 HS 10자리 digit 강제 — backend /trade/weight 가 H10 만 반환 (v2 §3)."""
    body = _tariff_body(["포스코"])
    body["seed_list"][0]["hscodes"] = ["390110"]  # 6자리 → 422
    assert client.post("/api/shock/tariff", json=body).status_code == 422


def test_shock_rate_any_finite_number(monkeypatch) -> None:
    """shock_rate 는 유한 실수면 제한 없음(음수·1 초과 허용) — NaN/±inf 만 422.

    (구 0~1 강제 폐지, 2026-08-26 요청. 음수 = 완화/감소 시나리오 부호 그대로 전파.)
    """
    import nice_shock.api.main as m

    monkeypatch.setattr(m, "fetch_weights", _grid_weights(1.0))
    for ok_rate in (-0.2, 1.5, 3.0):
        r = client.post("/api/shock/tariff", json=_tariff_body(["포스코"], shock_rate=ok_rate))
        assert r.status_code == 200, (ok_rate, r.text)
    # httpx 는 NaN 직렬화를 거부하므로 stdlib json 으로 비표준 리터럴(NaN/Infinity)
    # raw body 를 만들어 전송 — starlette 파서는 수용하고 pydantic 이 422 로 거부해야 한다.
    import json as _json

    for bad in (float("nan"), float("inf"), float("-inf")):
        r = client.post(
            "/api/shock/tariff",
            content=_json.dumps(_tariff_body(["포스코"], shock_rate=bad)),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 422, (bad, r.status_code)
    vol = {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "seed_list": [{"seed_id": "포스코", "total_amount": 1_000_000.0, "shock_rate": -0.2}],
        "direction": "export",
    }
    d = client.post("/api/shock/volume", json=vol)
    assert d.status_code == 200
    sm = {x["node_id"]: x["shock"] for x in d.json()["data_list"]}
    assert sm["포스코"] < 0  # 음수 주입 = 감소 방향 그대로 전파
    vol["seed_list"][0]["shock_rate"] = float("nan")
    r = client.post(
        "/api/shock/volume",
        content=_json.dumps(vol),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422


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
        "triple_list", "bse_yr", "shock_rate", "seed_list", "direction", "iokind",
    }
    assert set(TariffSeedIn.model_fields) == {"seed_id", "upche_cd", "total_amount", "hscodes"}
    assert set(VolumeRequest.model_fields) == {"triple_list", "seed_list", "direction", "iokind"}
    assert set(VolumeSeedIn.model_fields) == {"seed_id", "total_amount", "shock_rate"}
    assert set(ExcludedSeedOut.model_fields) == {"node_id", "reason"}


def test_tariff_isolated_seed_excluded(monkeypatch) -> None:
    """edgelist(from∪to)에 없는 시드는 전파 제외 + excluded_seeds(사유 포함) 보고.

    제외 시드는 data_list·total_shock 에 포함되지 않는다 (조용한 echo 금지 —
    시드/그래프 조립 불일치를 클라이언트가 즉시 인지).
    """
    import nice_shock.api.main as m

    monkeypatch.setattr(m, "fetch_weights", _grid_weights(1.0))
    d = client.post("/api/shock/tariff", json=_tariff_body(["포스코", "유령기업"])).json()
    assert [e["node_id"] for e in d["excluded_seeds"]] == ["유령기업"]
    assert "노드 집합" in d["excluded_seeds"][0]["reason"]
    ids = {x["node_id"] for x in d["data_list"]}
    assert "유령기업" not in ids
    # total_shock 에 고립 시드의 주입액이 합산되지 않음 (포스코 경로 합만)
    assert abs(d["total_shock"] - sum(x["shock"] for x in d["data_list"])) < 1e-9


def test_tariff_no_trade_record_excluded(monkeypatch) -> None:
    """전 품목 실적 없는 시드는 excluded_seeds 로 보고, 나머지는 정상 전파.

    조회 키가 upche_cd 임을 함께 검증 — mock 이 upche_cd("현대모비스_up") 로 분기.
    """
    import nice_shock.api.main as m

    def fake(bse_yr, upchecd_list, hskcode_list, exim):
        return {
            (u, h): 1.0
            for u in upchecd_list if u != "현대모비스_up"
            for h in hskcode_list
        }

    monkeypatch.setattr(m, "fetch_weights", fake)
    d = client.post("/api/shock/tariff", json=_tariff_body(["포스코", "현대모비스"])).json()
    assert [e["node_id"] for e in d["excluded_seeds"]] == ["현대모비스"]
    assert "실적 없음" in d["excluded_seeds"][0]["reason"]
    sm = {x["node_id"]: x["shock"] for x in d["data_list"]}
    assert "현대모비스" not in sm and "포스코" in sm  # 포스코 경로만 전파


def test_tariff_rate_api_unconfigured_503(monkeypatch) -> None:
    """RATE_API_URL 미설정이면 시드 단위가 아니라 요청 전체 503 (서비스 구성 문제)."""
    monkeypatch.delenv("RATE_API_URL", raising=False)
    r = client.post("/api/shock/tariff", json=_tariff_body(["포스코"]))
    assert r.status_code == 503
    assert "RATE_API_URL" in r.json()["detail"]


def test_rate_client_parses_and_filters(monkeypatch) -> None:
    """rate_client — 실계약 응답 파싱: 방향(exim) 필터·문자열 rate 변환·범위 위반 셀 버림."""
    import nice_shock.rate_client as rc

    payload = {
        "status": 0,
        "data": [
            {"bseYr": "2025", "tscdcg": "H10", "upchecd": "380130",
             "weightList": [
                 {"tseximdivcd": "0", "tscdvl": "7318160000", "tstrdwgt": "0.272135"},
                 {"tseximdivcd": "3", "tscdvl": "7318160000", "tstrdwgt": "0.002382"},
                 {"tseximdivcd": "0", "tscdvl": "8534009000", "tstrdwgt": "1.500000"},  # 위반
             ]},
        ],
        "message": None,
    }

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return payload

    sent: dict = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, body=json)
        return _Resp()

    monkeypatch.setenv("RATE_API_URL", "http://backend.test/trade/weight")
    monkeypatch.setattr(rc.httpx, "post", fake_post)
    w = rc.fetch_weights("2025", ["380130"], ["7318160000", "8534009000"], exim=rc.EXIM_EXPORT)
    assert sent["url"] == "http://backend.test/trade/weight"
    assert sent["body"] == {"bseYr": "2025", "upchecdList": ["380130"],
                            "hskcodeList": ["7318160000", "8534009000"]}
    # 수출("0") 행만 + 범위 위반 셀 제외
    assert w == {("380130", "7318160000"): 0.272135}
    w_imp = rc.fetch_weights("2025", ["380130"], ["7318160000"], exim=rc.EXIM_IMPORT)
    assert w_imp == {("380130", "7318160000"): 0.002382}


def test_rate_client_status_nonzero_unavailable(monkeypatch) -> None:
    """backend status≠0 은 서비스 수준 문제 — RateApiUnavailable (요청 전체 503)."""
    import pytest

    import nice_shock.rate_client as rc

    class _Resp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"status": -1, "data": None, "message": "internal"}

    monkeypatch.setenv("RATE_API_URL", "http://backend.test/trade/weight")
    monkeypatch.setattr(rc.httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(rc.RateApiUnavailable, match="status=-1"):
        rc.fetch_weights("2025", ["380130"], ["7318160000"], exim=rc.EXIM_EXPORT)


# ── rate-mock (backend /trade/weight 목업) ────────────────────────────────────


def test_rate_mock_shape_deterministic_and_bounded() -> None:
    """목업 — 실계약 응답 형상, 같은 입력엔 항상 같은 값, 0~1 범위, 방향별 상이."""
    from nice_shock.mock_rate_api import app as mock_app

    mc = TestClient(mock_app)
    body = {"bseYr": "2025", "upchecdList": ["184084"], "hskcodeList": ["3801300000"]}
    r1 = mc.post("/trade/weight", json=body)
    r2 = mc.post("/trade/weight", json=body)
    assert r1.status_code == 200
    assert r1.json() == r2.json()  # 결정적
    d = r1.json()
    assert d["status"] == 0
    row = d["data"][0]
    assert row["upchecd"] == "184084" and row["tscdcg"] == "H10"
    by_exim = {w["tseximdivcd"]: float(w["tstrdwgt"]) for w in row["weightList"]}
    assert set(by_exim) == {"0", "3"}  # 수출·수입 두 방향
    assert all(0.0 <= v <= 1.0 for v in by_exim.values())
    assert by_exim["0"] != by_exim["3"]  # 방향별 다른 값


def test_rate_mock_no_record_convention() -> None:
    """목업 규약 — '0000' 접두 hskcode 는 행 부재, '0000' 접두 upchecd 는 weightList 빈 배열."""
    from nice_shock.mock_rate_api import app as mock_app

    mc = TestClient(mock_app)
    d = mc.post("/trade/weight", json={
        "bseYr": "2025", "upchecdList": ["184084", "0000_none"],
        "hskcodeList": ["3801300000", "0000999999"],
    }).json()
    rows = {r["upchecd"]: r["weightList"] for r in d["data"]}
    assert {w["tscdvl"] for w in rows["184084"]} == {"3801300000"}  # '0000' 품목 부재
    assert rows["0000_none"] == []  # 실적 없는 업체
    assert mc.get("/health").json()["status"] == "ok"


def test_tariff_end_to_end_with_rate_mock(monkeypatch) -> None:
    """shock-server rate_client → rate-mock 실호출 경로 검증 (httpx.post 를 목업 앱으로 우회)."""
    import nice_shock.rate_client as rc
    from nice_shock.mock_rate_api import app as mock_app

    mc = TestClient(mock_app)
    monkeypatch.setenv("RATE_API_URL", "http://rate-mock.test/trade/weight")
    monkeypatch.setattr(
        rc.httpx, "post", lambda url, json, timeout: mc.post("/trade/weight", json=json)
    )
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


def test_cri_endpoint_not_exposed() -> None:
    """/api/cri 는 외부 비노출(2026-08-25 결정) — 라우트 미등록으로 404.

    계산 함수·스키마는 보존(main.cri 직접 호출은 동작), 재노출 시 데코레이터 주석 해제.
    """
    r = client.post("/api/cri", json={"nodes": _CRI_NODES, "edges": _CRI_EDGES})
    assert r.status_code == 404

    from nice_shock.api.main import CriRequest, cri

    d = cri(CriRequest(nodes=_CRI_NODES, edges=_CRI_EDGES)).model_dump()
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
