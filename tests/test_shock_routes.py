"""nice_graph 쇼크 라우터 — select_primary / assemble / propagate 엔드포인트 테스트.

DB 의존(screen/assemble) 은 라우터 모듈의 함수 심볼을 monkeypatch 로 격리해
**라우팅·요청검증·직렬화·에러매핑** 만 검증한다 (실 PG 불필요 → CI 어디서나 통과).
``/propagate`` 는 순수 함수라 실제 호출.

canned 반환값을 실제 dataclass 로 만들어, 라우터의 ``FirmOut(**vars(f))`` /
``AssembledNodeOut(**vars(n))`` 직렬화가 dataclass 필드와 어긋나면 (스키마 드리프트)
테스트가 깨지도록 설계.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from nice_graph.api.main import app
from nice_dbtool.assemble import AssembledNode, PropagationInput
from nice_dbtool.screen import ExposedFirm, PrimarySelectionResult

client = TestClient(app)

ROUTER = "nice_graph.api.routers.shock"


# ── 헬퍼: canned 결과 ─────────────────────────────────────────────────────


def _firm(upchecd: str, bizno: str, score: float) -> ExposedFirm:
    return ExposedFirm(
        upchecd=upchecd,
        bizno=bizno,
        korentrnm=f"회사{upchecd}",
        exposure_ratio=score * 100,
        amount_tier=7,
        score=score,
        n_cells=1,
    )


def _sql_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("down"))


# ── select_primary ────────────────────────────────────────────────────────


def test_select_primary_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = PrimarySelectionResult(
        hscode="8481",
        hs_digits=4,
        year=None,
        exim=None,
        firms=[_firm("184084", "5948801875", 0.96), _firm("305052", "4138106081", 0.90)],
    )
    monkeypatch.setattr(f"{ROUTER}._select_primary_firms", lambda *a, **k: canned)

    r = client.post("/api/shock/select_primary", json={"hscode": "8481", "top_k": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hs_digits"] == 4
    assert [f["bizno"] for f in body["firms"]] == ["5948801875", "4138106081"]
    assert body["firms"][0]["score"] == 0.96
    assert body["firms"][0]["korentrnm"] == "회사184084"


def test_select_primary_passes_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def spy(hscode: str, **kwargs):
        captured["hscode"] = hscode
        captured["kwargs"] = kwargs
        return PrimarySelectionResult(hscode=hscode, hs_digits=6, year="2024", exim="0")

    monkeypatch.setattr(f"{ROUTER}._select_primary_firms", spy)
    r = client.post(
        "/api/shock/select_primary",
        json={"hscode": "848180", "year": "2024", "exim": "0", "top_k": 3, "min_ratio": 10.0},
    )
    assert r.status_code == 200, r.text
    assert captured["hscode"] == "848180"
    assert captured["kwargs"]["year"] == "2024"
    assert captured["kwargs"]["exim"] == "0"
    assert captured["kwargs"]["top_k"] == 3
    assert captured["kwargs"]["min_ratio"] == 10.0


@pytest.mark.parametrize("hscode", ["", "1", "123", "12345678901"])
def test_select_primary_rejects_bad_hscode(hscode: str) -> None:
    # min_length=4 / max_length=10 검증 — DB 호출 전에 422
    r = client.post("/api/shock/select_primary", json={"hscode": hscode})
    assert r.status_code == 422


def test_select_primary_db_error_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise _sql_error()

    monkeypatch.setattr(f"{ROUTER}._select_primary_firms", boom)
    r = client.post("/api/shock/select_primary", json={"hscode": "8481"})
    assert r.status_code == 503
    assert "unreachable" in r.json()["detail"]


# ── assemble ──────────────────────────────────────────────────────────────


def _canned_assembled() -> PropagationInput:
    return PropagationInput(
        edges=[{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.85}],
        init_sub_graph={"A|1": 0.96},
        nodes=[
            AssembledNode("A|1", "A", "1", "회사A", True, 0.96),
            AssembledNode("B|2", "B", "2", "회사B", False, 0.0),
        ],
        depth=3,
        rate_kind="all_rate",
        within_subgraph=True,
        damping=0.85,
        warnings=["upchecd 미상 노드 1건"],
    )


def test_assemble_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{ROUTER}._assemble", lambda *a, **k: _canned_assembled())
    r = client.post(
        "/api/shock/assemble",
        json={"seeds": [{"bizno": "A", "upchecd": "1", "shock": 0.96}], "depth": 3},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["edges"] == [{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.85}]
    assert body["init_sub_graph"] == {"A|1": 0.96}
    assert body["damping"] == 0.85
    assert body["warnings"] == ["upchecd 미상 노드 1건"]
    # 복합키 ↔ (bizno,upchecd,기업명) 매핑이 nodes 로 노출되는지 (5단계 결과출력용)
    seed_node = next(n for n in body["nodes"] if n["is_seed"])
    assert (seed_node["bizno"], seed_node["upchecd"], seed_node["node_id"]) == ("A", "1", "A|1")


def test_assemble_forwards_per_seed_shock(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def spy(seeds, **kwargs):
        captured["seeds"] = list(seeds)
        captured["kwargs"] = kwargs
        return _canned_assembled()

    monkeypatch.setattr(f"{ROUTER}._assemble", spy)
    r = client.post(
        "/api/shock/assemble",
        json={
            "seeds": [
                {"bizno": "A", "upchecd": "1", "shock": 0.96},
                {"bizno": "B", "upchecd": "2", "shock": 0.40},
            ],
            "depth": 2,
            "within_subgraph": False,
            "damping": 0.7,
        },
    )
    assert r.status_code == 200, r.text
    assert captured["seeds"] == [("A", "1"), ("B", "2")]
    # score → per-seed shock(dict) 경로 검증
    assert captured["kwargs"]["seed_shock"] == {"A": 0.96, "B": 0.40}
    assert captured["kwargs"]["depth"] == 2
    assert captured["kwargs"]["within_subgraph"] is False
    assert captured["kwargs"]["damping"] == 0.7


@pytest.mark.parametrize("damping", [0.0, -0.1, 1.5])
def test_assemble_rejects_bad_damping(damping: float) -> None:
    # gt=0, le=1 검증 — DB 호출 전에 422
    r = client.post(
        "/api/shock/assemble",
        json={"seeds": [{"bizno": "A", "upchecd": "1"}], "damping": damping},
    )
    assert r.status_code == 422


def test_assemble_db_error_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise _sql_error()

    monkeypatch.setattr(f"{ROUTER}._assemble", boom)
    r = client.post(
        "/api/shock/assemble", json={"seeds": [{"bizno": "A", "upchecd": "1"}]}
    )
    assert r.status_code == 503


# ── propagate (순수 함수 — 실제 호출) ──────────────────────────────────────


def test_propagate_endpoint_runs_real() -> None:
    # 선형 체인 A→B→C, damping 0.5. 순수 계산이라 DB 불필요.
    r = client.post(
        "/api/shock/propagate",
        json={
            "edges": [
                {"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5},
                {"from_bizno": "B|2", "to_bizno": "C|3", "rate": 0.5},
            ],
            "init_sub_graph": {"A|1": 1.0},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["converged"] is True
    shock = {row["bizno"]: row["shock"] for row in body["shock_list"]}
    # A=1, B=0.5, C=0.25 (체인 감쇠) — 복합키가 그대로 통과되는지도 확인
    assert shock["A|1"] == pytest.approx(1.0)
    assert shock["B|2"] == pytest.approx(0.5)
    assert shock["C|3"] == pytest.approx(0.25)


# ── OpenAPI 계약 ──────────────────────────────────────────────────────────


def test_openapi_registers_shock_paths() -> None:
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"])
    expected = {
        "/api/shock/select_primary",
        "/api/shock/assemble",
        "/api/shock/propagate",
        "/api/shock/fetch_subgraph",
        "/api/shock/extract_first_target",
    }
    assert expected <= paths, expected - paths
