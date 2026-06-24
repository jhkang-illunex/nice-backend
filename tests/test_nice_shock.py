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
    from nice_shock.api.main import TariffRequest, VolumeRequest

    assert set(TariffRequest.model_fields) == {"triple_list", "seed_list", "shock_rate", "direction"}
    assert set(VolumeRequest.model_fields) == {
        "triple_list", "seed_list", "node_overrides", "direction",
    }


def test_volume_delta_is_one_plus_propagated() -> None:
    """거래량 변동 — δ 노드 factor=0.8(−20%) → shock=1+전파편차(무변동 노드는 1)."""
    res = run_volume(
        _TRIPLES, ["포스코"], [{"p1": "포스코", "w1": 0.8}], [0], pin_seeds=False
    )
    sm = {r["bizno"]: r["shock"] for r in res[0]["result"].shock_list}
    # 포스코 δ=-0.2 → shock=0.8. 지오는 포스코×1.0 전파 → 1+(-0.2)=0.8.
    assert abs(sm["포스코"] - 0.8) < 1e-9
    assert abs(sm["지오"] - 0.8) < 1e-9


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


def test_health() -> None:
    assert client.get("/health").json()["status"] == "ok"
