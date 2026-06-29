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
    """가중평균(비중 합 1.0)·시드 고정 → 삼성 = 주입 충격량 그대로."""
    res = run_tariff(_TRIPLES, ["포스코", "현대모비스"], -0.2, [0], pin_seeds=True)
    sm = {r["bizno"]: r["shock"] for r in res[0]["result"].shock_list}
    assert sm["삼성"] == -0.2
    assert sm["포스코"] == -0.2 and sm["현대모비스"] == -0.2  # pin 고정
    assert res[0]["result"].converged is True


def test_tariff_api_endpoint() -> None:
    # 입력: direction(import|export, 기본 import). 출력: direction·total_shock·data_list(node_id).
    body = {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "seed_list": ["포스코", "현대모비스"],
        "shock_rate": -0.2,
        "direction": "export",  # 매출(downstream) — 엣지 그대로
    }
    r = client.post("/api/shock/tariff", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["direction"] == "export"  # 입력 echo
    assert "iterations" not in d and "converged" not in d  # 간소화로 제거됨
    rows = d["data_list"]
    sm = {x["node_id"]: x["shock"] for x in rows}
    assert abs(sm["삼성"] + 0.2) < 1e-9
    dep = {x["node_id"]: x["depth"] for x in rows}  # depth: 시드=1, 홉당 +1
    assert dep["포스코"] == 1 and dep["현대모비스"] == 1
    assert dep["삼성"] == 2 and dep["지오"] == 2


def test_tariff_default_direction_is_import() -> None:
    """direction 미입력 시 import(매입) 기본."""
    body = {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "seed_list": ["포스코"], "shock_rate": -0.2,
    }
    d = client.post("/api/shock/tariff", json=body).json()
    assert d["direction"] == "import"


def test_tariff_input_schema() -> None:
    """입력은 4개(direction 단수)만 — pin_seeds/method/cycle_damping/directions 없음."""
    from nice_shock.api.main import OverrideIn, TariffRequest, VolumeRequest

    assert set(TariffRequest.model_fields) == {"triple_list", "seed_list", "shock_rate", "direction"}
    assert set(VolumeRequest.model_fields) == {
        "triple_list", "seed_list", "node_overrides", "direction",
    }
    # node_overrides 항목은 0-기준 delta (과거 1-기준 w1 폐기)
    assert set(OverrideIn.model_fields) == {"p1", "delta"}


def test_volume_delta_zero_based() -> None:
    """거래량 변동 — node delta=−0.2(0-기준 변동율) → 결과=변동율 −0.2(무변동 노드는 0).

    입·출력 모두 0-기준 (tariff shock_rate 와 통일). 과거 w1(1-기준 factor)·shock=1+δ 폐기.
    """
    res = run_volume(
        _TRIPLES, ["포스코"], [{"p1": "포스코", "delta": -0.2}], [0], pin_seeds=False
    )
    sm = {r["bizno"]: r["shock"] for r in res[0]["result"].shock_list}
    # 포스코 δ=−0.2 → 변동율 −0.2. 지오는 포스코×1.0 전파 → −0.2.
    assert abs(sm["포스코"] - (-0.2)) < 1e-9
    assert abs(sm["지오"] - (-0.2)) < 1e-9


def test_volume_api_zero_based() -> None:
    """공개 /api/shock/volume — node_overrides[{p1,delta}] 0-기준, 출력도 변동율(0=무변화)."""
    body = {
        "triple_list": [{"from": x["from"], "to": x["to"], "rate": x["rate"]} for x in _TRIPLES],
        "seed_list": ["포스코"],
        "node_overrides": [{"p1": "포스코", "delta": -0.2}],
        "direction": "export",
    }
    d = client.post("/api/shock/volume", json=body).json()
    sm = {x["node_id"]: x["shock"] for x in d["data_list"]}
    assert abs(sm["포스코"] - (-0.2)) < 1e-9  # 0-기준: 무변화=0, −20%=−0.2
    assert abs(sm["지오"] - (-0.2)) < 1e-9


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
    """공개 /api/shock/cri — 노드별 sell/buy 속성 + 네트워크 지표."""
    r = client.post("/api/shock/cri", json={"nodes": _CRI_NODES, "edges": _CRI_EDGES})
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
