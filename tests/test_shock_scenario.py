"""쇼크 시나리오 래퍼 — 방향(상류/하류)·가중치(A/B)·거래변화(g) 단위 테스트.

검증 축
  1. assemble 방향 — downstream(셀러→바이어) / upstream(바이어→셀러) 엣지 방향 + 정규화 src 전환
  2. assemble 가중치/오버라이드 — direction_weight(A/B)·edge_overrides(g) 가 rate 에 반영
  3. scenario — tariff(양방향), transaction_change(변화분 Δ = changed − baseline)
  4. 라우터 — /scenario 직렬화 + transaction_change 빈 overrides → 422

DB 의존(_fetch_induced_edges / _fetch_node_attrs)은 monkeypatch 로 격리한다 (실 PG 불필요).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import nice_graph.shock.assemble as asm_mod
import nice_graph.shock.scenario as scen_mod
from nice_graph.api.main import app
from nice_graph.shock.assemble import PropagationInput, assemble_propagation_input
from nice_graph.shock.propagate import ShockResult, ShockRow
from nice_graph.shock.scenario import (
    DirectionResult,
    RandomOverrideSpec,
    ScenarioResult,
    build_primary_secondary_random_overrides,
    run_scenario,
    run_tariff_shock,
    run_transaction_change,
    run_volume_shock,
)

client = TestClient(app)
ROUTER = "nice_graph.api.routers.shock"


# ── assemble 격리 헬퍼 ─────────────────────────────────────────────────────


def _patch_assemble_db(monkeypatch, rows, attrs):
    """assemble 의 두 DB 호출을 canned 로 교체. _fetch_induced_edges 의 src_col 캡처."""
    captured: dict = {}

    def fake_edges(seeds, depth, trade_year, src_col="from_bizno", chapters=None):
        captured["src_col"] = src_col
        captured["seeds"] = list(seeds)
        captured["chapters"] = chapters
        return rows

    monkeypatch.setattr(asm_mod, "_fetch_induced_edges", fake_edges)
    monkeypatch.setattr(asm_mod, "_fetch_node_attrs", lambda biznos: attrs)
    return captured


# 단일 엣지 A(셀러)→B(바이어), amt=100, source 정규화 합=100 (A 의 유일 outgoing).
_ROWS = [("A", "B", 100.0, 100.0, 100.0)]
_ATTRS = {"A": ("1", "회사A"), "B": ("2", "회사B")}


# ── 1. 방향 ────────────────────────────────────────────────────────────────


def test_downstream_keeps_seller_to_buyer(monkeypatch) -> None:
    cap = _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    out = assemble_propagation_input(
        [("A", "1")], direction="downstream", damping=0.5, direction_weight=1.0
    )
    assert cap["src_col"] == "from_bizno"  # 셀러 기준 정규화
    assert out.edges == [{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5}]
    assert out.direction == "downstream"


def test_upstream_reverses_to_buyer_to_seller(monkeypatch) -> None:
    cap = _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    out = assemble_propagation_input(
        [("A", "1")], direction="upstream", damping=0.5, direction_weight=1.0
    )
    assert cap["src_col"] == "to_bizno"  # 바이어 기준 정규화 (수렴 유지)
    # 방향 뒤집힘: 바이어(B)→셀러(A)
    assert out.edges == [{"from_bizno": "B|2", "to_bizno": "A|1", "rate": 0.5}]
    assert out.direction == "upstream"


# ── 2. 가중치 / 오버라이드 ──────────────────────────────────────────────────


def test_direction_weight_scales_rate(monkeypatch) -> None:
    _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    out = assemble_propagation_input(
        [("A", "1")], direction="downstream", damping=0.5, direction_weight=0.4
    )
    # rate = direction_weight(0.4) · damping(0.5) · (100/100) = 0.2
    assert out.edges[0]["rate"] == pytest.approx(0.2)
    assert out.direction_weight == 0.4


def test_edge_override_applies_g_by_storage_direction(monkeypatch) -> None:
    _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    # 저장방향 (A,B) 키로 g=0.25. upstream 이어도 원 (from,to) 로 매칭.
    out = assemble_propagation_input(
        [("A", "1")],
        direction="upstream",
        damping=1.0,
        direction_weight=1.0,
        edge_overrides={("A", "B"): 0.25},
    )
    # rate = 1·1·(100/100)·0.25 = 0.25, 방향은 upstream → B|2 → A|1
    assert out.edges == [{"from_bizno": "B|2", "to_bizno": "A|1", "rate": 0.25}]


def test_weight_times_damping_over_one_warns(monkeypatch) -> None:
    _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    out = assemble_propagation_input(
        [("A", "1")], direction="downstream", damping=0.9, direction_weight=2.0
    )
    assert any("발산" in w for w in out.warnings)


@pytest.mark.parametrize("bad", ["sideways", "", "UP"])
def test_invalid_direction_raises(monkeypatch, bad: str) -> None:
    _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    with pytest.raises(ValueError):
        assemble_propagation_input([("A", "1")], direction=bad)  # type: ignore[arg-type]


# ── 3. scenario ────────────────────────────────────────────────────────────


def _fake_assembled(edges, init) -> PropagationInput:
    return PropagationInput(edges=edges, init_sub_graph=init, nodes=[], depth=3)


def test_tariff_runs_both_directions(monkeypatch) -> None:
    captured: list = []

    def fake_assemble(seeds, **kw):
        captured.append(kw)
        return _fake_assembled([], {"A|1": 1.0})

    monkeypatch.setattr(scen_mod, "assemble_propagation_input", fake_assemble)
    res = run_tariff_shock([("A", "1")], weight_a=0.8, weight_b=0.6)

    assert res.scenario == "tariff"
    assert [d.direction for d in res.directions] == ["upstream", "downstream"]
    # 문서 기준 라벨: upstream=매입 파급, downstream=매출 파급
    assert [d.effect_label for d in res.directions] == ["매입 파급", "매출 파급"]
    # 가중치: 매출(downstream)=A, 매입(upstream)=B
    weights = {kw["direction"]: kw["direction_weight"] for kw in captured}
    assert weights == {"upstream": 0.6, "downstream": 0.8}
    # init 만 있는 그래프 → 각 시드 shock 그대로
    assert res.directions[0].result.shock_list == [{"bizno": "A|1", "shock": 1.0}]


def test_transaction_change_returns_delta(monkeypatch) -> None:
    """baseline(원W) 대비 changed(수정W=in-memory g)의 노드별 Δ + assemble 1회/방향(E1)."""
    calls = {"n": 0}

    def fake_assemble(seeds, *, edge_overrides=None, **kw):
        calls["n"] += 1
        # baseline 은 항상 셀러A→바이어B(rate 0.5)를 포함. E1 은 g 를 in-memory 로 적용.
        return _fake_assembled(
            [{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5}], {"A|1": 1.0}
        )

    monkeypatch.setattr(scen_mod, "assemble_propagation_input", fake_assemble)
    res = run_transaction_change(
        [("A", "1")], edge_overrides={("A", "B"): 0.5}, directions=["downstream"]
    )

    delta = {r["bizno"]: r["shock"] for r in res.directions[0].result.shock_list}
    assert delta["A|1"] == pytest.approx(0.0)  # 시드 자기 충격 동일 → Δ=0
    assert delta["B|2"] == pytest.approx(-0.25)  # 거래 0.5배 → 하류 전파 0.5→0.25 감소
    assert any("difference-of-runs" in w for w in res.warnings)
    assert calls["n"] == 1  # E1: 방향당 baseline 1회만 (수정 W 는 in-memory)


def test_transaction_change_increase_positive_delta(monkeypatch) -> None:
    """g>1(증가) → 하류 전파 증가 → Δ>0 + g>1·Σ_out>1 경고."""
    def fake_assemble(seeds, *, edge_overrides=None, **kw):
        # baseline rate 0.8(큰 비중). g=1.5 → 1.2 로 Σ_out>1.
        return _fake_assembled(
            [{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.8}], {"A|1": 1.0}
        )

    monkeypatch.setattr(scen_mod, "assemble_propagation_input", fake_assemble)
    res = run_transaction_change(
        [("A", "1")], edge_overrides={("A", "B"): 1.5}, directions=["downstream"]
    )
    delta = {r["bizno"]: r["shock"] for r in res.directions[0].result.shock_list}
    assert delta["B|2"] == pytest.approx(0.4)  # 0.8→1.2 → 하류 전파 +0.4 증가
    assert any("g>1" in w for w in res.warnings)            # 증가 경고
    assert any("Σ_out" in w and ">1" in w for w in res.warnings)  # Σ_out>1 발산 위험


def test_transaction_change_all_g_one_warns(monkeypatch) -> None:
    monkeypatch.setattr(
        scen_mod,
        "assemble_propagation_input",
        lambda seeds, **kw: _fake_assembled(
            [{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5}], {"A|1": 1.0}
        ),
    )
    res = run_transaction_change(
        [("A", "1")], edge_overrides={("A", "B"): 1.0}, directions=["downstream"]
    )
    assert any("변화 없음" in w for w in res.warnings)
    # g=1.0 → Δ 전부 0
    assert all(abs(r["shock"]) < 1e-12 for r in res.directions[0].result.shock_list)


def test_transaction_change_empty_overrides_raises() -> None:
    with pytest.raises(ValueError):
        run_transaction_change([("A", "1")], edge_overrides={})


# ── 4. 라우터 ──────────────────────────────────────────────────────────────


def _canned_scenario() -> ScenarioResult:
    asm = _fake_assembled(
        [{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5}], {"A|1": 1.0}
    )
    asm.nodes = []  # n_nodes=0, n_edges=1 직렬화 확인용
    result = ShockResult(
        shock_list=[ShockRow(bizno="A|1", shock=1.0), ShockRow(bizno="B|2", shock=0.5)],
        total_shock=1.5,
        iterations=1,
        converged=True,
    )
    return ScenarioResult(
        "tariff",
        [DirectionResult("downstream", "매출 파급", 0.8, asm, result)],  # 문서: 매출=downstream
        warnings=["w1"],
    )


def test_scenario_endpoint_serializes(monkeypatch) -> None:
    monkeypatch.setattr(f"{ROUTER}._run_scenario", lambda *a, **k: _canned_scenario())
    r = client.post(
        "/api/shock/scenario",
        json={
            "scenario": "tariff",
            "seeds": [{"bizno": "A", "upchecd": "1", "shock": 1.0}],
            "weight_a": 0.8,
            "weight_b": 0.6,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario"] == "tariff"
    d0 = body["directions"][0]
    assert d0["direction"] == "downstream"
    assert d0["effect_label"] == "매출 파급"
    assert d0["weight"] == 0.8
    assert d0["n_edges"] == 1 and d0["n_nodes"] == 0
    assert {row["bizno"]: row["shock"] for row in d0["shock_list"]} == {"A|1": 1.0, "B|2": 0.5}


def test_scenario_transaction_change_requires_overrides() -> None:
    # 실제 함수가 빈 overrides 에 ValueError → 라우터가 422 로 매핑 (DB 미접근).
    r = client.post(
        "/api/shock/scenario",
        json={
            "scenario": "transaction_change",
            "seeds": [{"bizno": "A", "upchecd": "1"}],
            "edge_overrides": [],
        },
    )
    assert r.status_code == 422, r.text


def test_scenario_passes_directions_and_weights(monkeypatch) -> None:
    captured: dict = {}

    def spy(scenario, seeds, **kwargs):
        captured["scenario"] = scenario
        captured["seeds"] = list(seeds)
        captured["kwargs"] = kwargs
        return _canned_scenario()

    monkeypatch.setattr(f"{ROUTER}._run_scenario", spy)
    r = client.post(
        "/api/shock/scenario",
        json={
            "scenario": "tariff",
            "seeds": [{"bizno": "A", "upchecd": "1", "shock": 0.9}],
            "directions": ["downstream"],
            "weight_a": 0.7,
            "weight_b": 0.5,
            "depth": 2,
        },
    )
    assert r.status_code == 200, r.text
    assert captured["scenario"] == "tariff"
    assert captured["seeds"] == [("A", "1")]
    assert captured["kwargs"]["directions"] == ["downstream"]
    assert captured["kwargs"]["weight_a"] == 0.7
    assert captured["kwargs"]["weight_b"] == 0.5
    assert captured["kwargs"]["depth"] == 2
    assert captured["kwargs"]["seed_shock"] == {"A": 0.9}


# ── 5. 1차↔2차 매출/매입 랜덤 override 생성기 ─────────────────────────────────

# downstream(셀러→바이어) 조립 결과를 흉내낸 canned 엣지. 복합키 'bizno|upchecd'.
_GEN_SEEDS = [("S1", "u1"), ("S2", "u2")]
_GEN_EDGES = [
    {"from_bizno": "S1|u1", "to_bizno": "A|ua", "rate": 0.1},   # 매출(S1 판매→2차 A)
    {"from_bizno": "S2|u2", "to_bizno": "A|ua", "rate": 0.1},   # 매출(S2 판매→2차 A)
    {"from_bizno": "B|ub", "to_bizno": "S1|u1", "rate": 0.1},   # 매입(2차 B 판매→S1)
    {"from_bizno": "S1|u1", "to_bizno": "S2|u2", "rate": 0.1},  # 1차↔1차 (제외)
    {"from_bizno": "A|ua", "to_bizno": "C|uc", "rate": 0.1},    # 2차↔3차 (제외)
]


def _patch_gen(monkeypatch):
    def fake(seeds, **kw):
        return PropagationInput(edges=list(_GEN_EDGES), init_sub_graph={}, nodes=[], depth=3)
    monkeypatch.setattr(scen_mod, "assemble_propagation_input", fake)


def test_random_both_classifies_1to2(monkeypatch) -> None:
    _patch_gen(monkeypatch)
    ov = build_primary_secondary_random_overrides(_GEN_SEEDS, spec=RandomOverrideSpec(side="both", seed=1))
    assert set(ov) == {("S1", "A"), ("S2", "A"), ("B", "S1")}  # 1차↔1차·2차↔3차 제외


def test_random_sales_only(monkeypatch) -> None:
    _patch_gen(monkeypatch)
    ov = build_primary_secondary_random_overrides(_GEN_SEEDS, spec=RandomOverrideSpec(side="sales", seed=1))
    assert set(ov) == {("S1", "A"), ("S2", "A")}


def test_random_purchase_only(monkeypatch) -> None:
    _patch_gen(monkeypatch)
    ov = build_primary_secondary_random_overrides(_GEN_SEEDS, spec=RandomOverrideSpec(side="purchase", seed=1))
    assert set(ov) == {("B", "S1")}


def test_random_only_firms_subset(monkeypatch) -> None:
    _patch_gen(monkeypatch)
    ov = build_primary_secondary_random_overrides(
        _GEN_SEEDS, spec=RandomOverrideSpec(side="both", only_firms=("S1",), seed=1)
    )
    assert set(ov) == {("S1", "A"), ("B", "S1")}  # S2 매출 제외


def test_random_seed_reproducible(monkeypatch) -> None:
    _patch_gen(monkeypatch)
    a = build_primary_secondary_random_overrides(_GEN_SEEDS, spec=RandomOverrideSpec(seed=7))
    b = build_primary_secondary_random_overrides(_GEN_SEEDS, spec=RandomOverrideSpec(seed=7))
    c = build_primary_secondary_random_overrides(_GEN_SEEDS, spec=RandomOverrideSpec(seed=8))
    assert a == b and a != c


def test_random_range_respected(monkeypatch) -> None:
    _patch_gen(monkeypatch)
    ov = build_primary_secondary_random_overrides(
        _GEN_SEEDS, spec=RandomOverrideSpec(low=0.2, high=0.5, seed=3)
    )
    assert ov and all(0.2 <= g <= 0.5 for g in ov.values())


@pytest.mark.parametrize("lo,hi", [(0.5, 0.2), (-0.1, 0.5), (0.5, 3.5)])
def test_random_bad_range_raises(monkeypatch, lo: float, hi: float) -> None:
    _patch_gen(monkeypatch)
    with pytest.raises(ValueError):
        build_primary_secondary_random_overrides(_GEN_SEEDS, spec=RandomOverrideSpec(low=lo, high=hi))


def test_random_increase_range_allowed(monkeypatch) -> None:
    """g>1(증가) 범위는 MAX_OVERRIDE_FACTOR 이하면 허용 (예: 1.1~1.5)."""
    _patch_gen(monkeypatch)
    ov = build_primary_secondary_random_overrides(
        _GEN_SEEDS, spec=RandomOverrideSpec(low=1.1, high=1.5, seed=1)
    )
    assert ov and all(1.1 <= g <= 1.5 for g in ov.values())


# ── 6. 엔드포인트 random_override 경로 ───────────────────────────────────────


def test_scenario_random_override_endpoint(monkeypatch) -> None:
    captured: dict = {}

    def spy(scenario, seeds, *, random_spec=None, edge_overrides=None, **kw):
        captured["scenario"] = scenario
        captured["random_spec"] = random_spec
        return ScenarioResult(
            "transaction_change",
            _canned_scenario().directions,
            ["w"],
            applied_overrides={("S1", "A"): 0.3, ("B", "S1"): 0.7},
        )

    monkeypatch.setattr(f"{ROUTER}._run_scenario", spy)

    r = client.post(
        "/api/shock/scenario",
        json={
            "scenario": "transaction_change",
            "seeds": [{"bizno": "S1", "upchecd": "u1"}],
            "random_override": {"side": "sales", "low": 0.0, "high": 1.0, "seed": 42},
        },
    )
    assert r.status_code == 200, r.text
    # 라우터가 random_override → RandomOverrideSpec 로 만들어 run_scenario 에 전달
    assert captured["random_spec"].side == "sales" and captured["random_spec"].seed == 42
    # 결과의 applied_overrides 가 응답에 직렬화(정렬)되어 노출
    ao = {(o["from_bizno"], o["to_bizno"]): o["factor"] for o in r.json()["applied_overrides"]}
    assert ao == {("S1", "A"): 0.3, ("B", "S1"): 0.7}


def test_run_scenario_random_dispatch(monkeypatch) -> None:
    # run_scenario 가 random_spec → 랜덤 생성 → run_transaction_change 로 디스패치
    captured: dict = {}

    def fake_build(seeds, *, spec, **kw):
        captured["spec"] = spec
        return {("S1", "A"): 0.3}

    def fake_txn(seeds, *, edge_overrides, **kw):
        captured["ov"] = edge_overrides
        return ScenarioResult(
            "transaction_change", [], ["w"], applied_overrides=dict(edge_overrides)
        )

    monkeypatch.setattr(scen_mod, "build_primary_secondary_random_overrides", fake_build)
    monkeypatch.setattr(scen_mod, "run_transaction_change", fake_txn)
    res = run_scenario(
        "transaction_change", [("S1", "u1")], random_spec=RandomOverrideSpec(side="sales", seed=7)
    )
    assert captured["spec"].side == "sales" and captured["spec"].seed == 7
    assert captured["ov"] == {("S1", "A"): 0.3}
    assert res.applied_overrides == {("S1", "A"): 0.3}


# ── 7. normalize(source/counterparty) 옵션 ───────────────────────────────────


def test_normalize_source_partition(monkeypatch) -> None:
    # source(기본): downstream→from, upstream→to (전파 소스 기준)
    cap = _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    out = assemble_propagation_input([("A", "1")], direction="upstream", normalize="source")
    assert cap["src_col"] == "to_bizno"
    assert out.normalize == "source"


def test_normalize_counterparty_flips_partition(monkeypatch) -> None:
    # counterparty: 분모 컬럼이 거래상대로 뒤집힘 (매출/매입 비중 라벨)
    cap = _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    out = assemble_propagation_input([("A", "1")], direction="downstream", normalize="counterparty")
    assert cap["src_col"] == "to_bizno"  # 바이어 총매입 분모
    assert out.normalize == "counterparty"

    cap2 = _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    assemble_propagation_input([("A", "1")], direction="upstream", normalize="counterparty")
    assert cap2["src_col"] == "from_bizno"  # 셀러 총매출 분모


def test_counterparty_warns_convergence(monkeypatch) -> None:
    _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    out = assemble_propagation_input([("A", "1")], direction="downstream", normalize="counterparty")
    assert any("수렴" in w for w in out.warnings)


@pytest.mark.parametrize("bad", ["src", "", "COUNTERPARTY"])
def test_invalid_normalize_raises(monkeypatch, bad: str) -> None:
    _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    with pytest.raises(ValueError):
        assemble_propagation_input([("A", "1")], normalize=bad)  # type: ignore[arg-type]


def test_scenario_endpoint_passes_normalize(monkeypatch) -> None:
    captured: dict = {}

    def spy(scenario, seeds, **kwargs):
        captured["kwargs"] = kwargs
        return _canned_scenario()

    monkeypatch.setattr(f"{ROUTER}._run_scenario", spy)
    r = client.post(
        "/api/shock/scenario",
        json={
            "scenario": "tariff",
            "seeds": [{"bizno": "A", "upchecd": "1"}],
            "normalize": "counterparty",
        },
    )
    assert r.status_code == 200, r.text
    assert captured["kwargs"]["normalize"] == "counterparty"


# ── 8. 코드리뷰 회귀 (C1·C2·C3) ──────────────────────────────────────────────


def test_seed_duplicate_bizno_not_double_counted(monkeypatch) -> None:
    # C1: 동일 bizno 가 복수 upchecd pair → init 한 번만 (이중계상 방지)
    _patch_assemble_db(monkeypatch, [], {"B1": ("U1", "회사B1")})
    out = assemble_propagation_input([("B1", "U1"), ("B1", "U2")], seed_shock=1.0)
    assert out.init_sub_graph == {"B1|U1": 1.0}  # 2.0 이면 이중계상 버그


def test_scenario_rejects_empty_directions() -> None:
    # C2: directions=[] → 422 (min_length=1)
    r = client.post(
        "/api/shock/scenario",
        json={
            "scenario": "tariff",
            "seeds": [{"bizno": "A", "upchecd": "1"}],
            "directions": [],
        },
    )
    assert r.status_code == 422, r.text


def test_random_only_firms_no_intersection_raises(monkeypatch) -> None:
    # C3: only_firms 가 시드와 교집합 없으면 명확한 ValueError
    _patch_gen(monkeypatch)
    with pytest.raises(ValueError, match="교집합"):
        build_primary_secondary_random_overrides(
            _GEN_SEEDS, spec=RandomOverrideSpec(only_firms=("ZZZ",), seed=1)
        )


# ── industry_code (HS chapter 산업 필터) ──────────────────────────────────────


def test_resolve_industry_chapters_mapping() -> None:
    from nice_graph.shock.assemble import resolve_industry_chapters as r

    assert r(None) is None
    assert r(["전체"]) is None            # 전체 포함 → 필터 없음
    assert r("화학") == [f"{i:02d}" for i in range(28, 40)]
    assert r("기계") == ["84"]
    assert r("에너지") == ["27"]
    # 여러 개 → 합집합(정렬·중복제거)
    assert r(["화학", "철강/금속"]) == sorted(
        {f"{i:02d}" for i in range(28, 40)} | {f"{i:02d}" for i in range(72, 84)}
    )
    with pytest.raises(ValueError, match="알 수 없는 industry_code"):
        r(["없는산업"])


def test_industry_all_skips_filter(monkeypatch) -> None:
    """industry_code=['전체'] → chapters=None, 시드 필터 DB 호출 없음."""
    cap = _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)

    def boom(*a, **k):  # _filter_biznos_by_industry 가 불리면 실패
        raise AssertionError("전체인데 산업 필터 DB 호출됨")

    monkeypatch.setattr(asm_mod, "_filter_biznos_by_industry", boom)
    out = assemble_propagation_input([("A", "1")], industry_code=["전체"])
    assert cap["chapters"] is None
    assert out.edges  # 정상 조립


def test_industry_filter_threads_chapters(monkeypatch) -> None:
    """industry_code=['화학'] → 해당 chapters 가 _fetch_induced_edges 로 전달."""
    cap = _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    # 시드 A 는 산업 통과로 간주
    monkeypatch.setattr(asm_mod, "_filter_biznos_by_industry", lambda biznos, ch: set(biznos))
    assemble_propagation_input([("A", "1")], industry_code=["화학"])
    assert cap["chapters"] == [f"{i:02d}" for i in range(28, 40)]


def test_industry_filter_drops_unclassified_seed(monkeypatch) -> None:
    """산업 미통과 시드는 init/노드에서 제외 → 빈 그래프."""
    cap = _patch_assemble_db(monkeypatch, _ROWS, _ATTRS)
    monkeypatch.setattr(asm_mod, "_filter_biznos_by_industry", lambda biznos, ch: set())
    out = assemble_propagation_input([("A", "1")], industry_code=["화학"])
    assert out.init_sub_graph == {}          # 시드 제외 → 초기 충격 없음
    assert any("제외" in w or "0" in w for w in out.warnings)
    assert cap["seeds"] == []                # 필터 후 시드 없음


# ── 7. volume (거래량 변동 v2 — 편차 전파·시드 고정) ──────────────────────────


def _fake_volume_assemble(monkeypatch, edges):
    """assemble_propagation_input 을 대체 — init 을 seed_shock(δ) 맵에서 구성."""
    def fake(seeds, *, seed_shock, edge_overrides=None, **kw):
        init = {f"{b}|1": float(seed_shock[b]) for b in seed_shock}
        return _fake_assembled(list(edges), init)
    monkeypatch.setattr(scen_mod, "assemble_propagation_input", fake)


def test_volume_neutral_keeps_one(monkeypatch) -> None:
    """모든 multiplier=1.0 → δ=0 → 전 노드 shock=1(무변화)."""
    _fake_volume_assemble(monkeypatch, [{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5}])
    res = run_volume_shock([("A", "1")], multipliers={"A": 1.0}, directions=["downstream"])
    vals = {r["bizno"]: r["shock"] for r in res.directions[0].result.shock_list}
    assert all(abs(v - 1.0) < 1e-9 for v in vals.values())


def test_volume_propagates_deviation(monkeypatch) -> None:
    """시드 A 매출 −20%(m=0.8) → δ=−0.2, B(rate0.5) shock=1+0.5·(−0.2)=0.9."""
    _fake_volume_assemble(monkeypatch, [{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5}])
    res = run_volume_shock([("A", "1")], multipliers={"A": 0.8}, directions=["downstream"])
    vals = {r["bizno"]: r["shock"] for r in res.directions[0].result.shock_list}
    assert vals["A|1"] == pytest.approx(0.8)   # 시드 = 입력 m (incoming 없음)
    assert vals["B|2"] == pytest.approx(0.9)   # 1 + 0.5·(−0.2)


def test_volume_pin_vs_feedback(monkeypatch) -> None:
    """2-순환 A↔B 에서 pin=True 면 시드 A 고정(=m), False 면 되돌이로 증폭."""
    cyc = [
        {"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5},
        {"from_bizno": "B|2", "to_bizno": "A|1", "rate": 0.5},  # 되돌이
    ]
    _fake_volume_assemble(monkeypatch, cyc)
    pinned = run_volume_shock([("A", "1")], multipliers={"A": 0.8},
                              directions=["downstream"], pin_seeds=True)
    fed = run_volume_shock([("A", "1")], multipliers={"A": 0.8},
                           directions=["downstream"], pin_seeds=False)
    a_pin = {r["bizno"]: r["shock"] for r in pinned.directions[0].result.shock_list}["A|1"]
    a_fed = {r["bizno"]: r["shock"] for r in fed.directions[0].result.shock_list}["A|1"]
    assert a_pin == pytest.approx(0.8)   # 고정: 입력 그대로
    assert a_fed < 0.8                   # 피드백: 되돌이로 더 감소(증폭)


def test_volume_via_run_scenario(monkeypatch) -> None:
    """run_scenario('volume') 디스패치 경로."""
    _fake_volume_assemble(monkeypatch, [{"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5}])
    res = run_scenario("volume", [("A", "1")], multipliers={"A": 0.8}, directions=["downstream"])
    assert res.scenario == "volume"
    vals = {r["bizno"]: r["shock"] for r in res.directions[0].result.shock_list}
    assert vals["A|1"] == pytest.approx(0.8)


def test_volume_edge_multipliers_share_weighted(monkeypatch) -> None:
    """엣지 단위 볼륨 — 파트너에 share×(g−1) 주입, 허브 자신은 불변(0%)."""
    # A(허브)→B(rate0.5), A→C(rate0.5). 엣지 A→B +20%(g=1.2).
    edges = [
        {"from_bizno": "A|1", "to_bizno": "B|2", "rate": 0.5},
        {"from_bizno": "A|1", "to_bizno": "C|3", "rate": 0.5},
    ]
    def fake(seeds, *, seed_shock, edge_overrides=None, **kw):
        from nice_graph.shock.assemble import AssembledNode
        init = {f"{b}|1": float(seed_shock[b]) for b in seed_shock}
        def _n(nid, b, up, nm):
            return AssembledNode(node_id=nid, bizno=b, upchecd=up, korentrnm=nm,
                                 is_seed=(b in seed_shock), seed_shock=init.get(nid, 0.0))
        return PropagationInput(edges=edges, init_sub_graph=init, depth=2, nodes=[
            _n("A|1", "A", "1", "허브"), _n("B|2", "B", "2", "삼성"), _n("C|3", "C", "3", "LG"),
        ])
    monkeypatch.setattr(scen_mod, "assemble_propagation_input", fake)
    # B 의 매입 중 A 비중 = 0.4 (share)
    monkeypatch.setattr(scen_mod, "edge_in_shares", lambda keys, ty: {("A", "B"): 0.4})

    res = run_volume_shock([("A", "1")], edge_multipliers={("A", "B"): 1.2},
                           directions=["downstream"], pin_seeds=True)
    by = {r["bizno"]: r["shock"] for r in res.directions[0].result.shock_list}
    assert by["A|1"] == pytest.approx(1.0)          # 허브 불변(δ=0)
    assert by["B|2"] == pytest.approx(1.0 + 0.4 * 0.2)  # share0.4 × +20% = +8%
