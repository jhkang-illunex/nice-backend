"""nice_graph.shock.propagate — propagate_shock 단위 테스트.

검증 축
  1. 경계 케이스  — 빈 edges / 빈 init / 고립 노드
  2. 수학적 불변식 — 선형 체인·자기루프의 해석적 해와 수치 결과 일치
  3. 안전장치     — epsilon 컷오프 / max_iter 도달(converged=False)
  4. 선형성       — 복수 시드의 효과가 단일 시드 합과 같아야 함
"""

from __future__ import annotations

import math

import pytest

from nice_graph.shock.propagate import (
    ShockResult,
    propagate_shock,
    propagate_shock_scc,
)

# ── 헬퍼 ──────────────────────────────────────────────────────────────────


def _edge(src: str, tgt: str, rate: float) -> dict:
    return {"from_bizno": src, "to_bizno": tgt, "rate": rate}


def _shock_map(result: ShockResult) -> dict[str, float]:
    return {row["bizno"]: row["shock"] for row in result.shock_list}


# ── 경계 케이스 ────────────────────────────────────────────────────────────


def test_empty_init_returns_empty_result() -> None:
    result = propagate_shock(edges=[_edge("A", "B", 0.5)], init_sub_graph={})
    assert result.shock_list == []
    assert result.total_shock == 0.0
    assert result.iterations == 0
    assert result.converged is True


def test_empty_edges_returns_init_only() -> None:
    result = propagate_shock(edges=[], init_sub_graph={"A": 1.0, "B": 2.0})
    sm = _shock_map(result)
    assert sm == pytest.approx({"A": 1.0, "B": 2.0})
    assert result.total_shock == pytest.approx(3.0)
    assert result.iterations == 0
    assert result.converged is True


def test_isolated_node_with_no_outgoing_edge() -> None:
    """init 노드가 outgoing edge 가 없으면 자신 외 전파 없음."""
    result = propagate_shock(
        edges=[_edge("X", "Y", 0.9)],  # 별개 엣지
        init_sub_graph={"A": 5.0},
    )
    sm = _shock_map(result)
    assert sm == pytest.approx({"A": 5.0})
    assert result.iterations == 0


# ── 사이클 포함 원시 Neumann (Leontief) — 회귀 고정 ────────────────────────


def test_raw_weight_neumann_with_cycle_matches_leontief() -> None:
    """원시 가중치(비정규화·damping=1) Σ Rᵏ·init = Leontief 역행렬 해.

    A↔B 사이클 + A→D→{B,E}, B→C 분기. 행 합 < 1(A:0.5,B:0.7,D:0.7)이라 ρ(R)<1
    로 수렴. 기대값은 (I−Rᵀ)⁻¹ 직접해(검증된 값). normalize='none' 모드가 재현해야
    할 기준 — assemble 의 거래비율을 rate 로 그대로 넣으면 이 거동이 된다.
    """
    edges = [
        _edge("A", "B", 0.3), _edge("A", "D", 0.2),
        _edge("D", "B", 0.3), _edge("D", "E", 0.4),
        _edge("B", "C", 0.5), _edge("B", "A", 0.2),
    ]
    result = propagate_shock(edges=edges, init_sub_graph={"A": 100.0})
    sm = _shock_map(result)
    assert sm == pytest.approx(
        {"A": 107.758621, "B": 38.793103, "C": 19.396552,
         "D": 21.551724, "E": 8.620690},
        rel=1e-5,
    )
    assert result.converged is True


# ── SCC 닫힌해 엔진 (비반복 + 조건부 damping) ──────────────────────────────


def test_scc_closed_form_matches_iterative_on_convergent_graph() -> None:
    """ρ<1 그래프: SCC 닫힌해(반복 0회) = 반복 엔진 = Leontief 직접해."""
    edges = [
        _edge("A", "B", 0.3), _edge("A", "D", 0.2),
        _edge("D", "B", 0.3), _edge("D", "E", 0.4),
        _edge("B", "C", 0.5), _edge("B", "A", 0.2),
    ]
    res = propagate_shock_scc(edges=edges, init_sub_graph={"A": 100.0})
    sm = {r["bizno"]: r["shock"] for r in res.shock_list}
    assert sm == pytest.approx(
        {"A": 107.758621, "B": 38.793103, "C": 19.396552,
         "D": 21.551724, "E": 8.620690}, rel=1e-5,
    )
    assert res.iterations == 0          # 전역 반복 없음
    assert res.converged is True
    assert res.damped_cycles == []      # ρ<1 → damping 불필요


def test_scc_conditional_damping_makes_unit_cycle_finite() -> None:
    """닫힌 사이클 r=1 (Σ_out=1·완전복귀): 반복은 발산, SCC는 조건부 damping(0.95)로 유한.

    a→b 0.1, b→c 0.4, b→d 0.6, c→b 1, d→b 1. 충격 7 at a.
    b 주입 = 0.7, 사이클 왕복배율 r=1.0 → ρ(M_S)=1 → ×0.95 → ρ=0.95.
    b 총충격 = 0.7/(1−0.95²) = 0.7/0.0975 ≈ 7.1795.
    """
    edges = [
        _edge("a", "b", 0.1), _edge("b", "c", 0.4), _edge("b", "d", 0.6),
        _edge("c", "b", 1.0), _edge("d", "b", 1.0),
    ]
    res = propagate_shock_scc(edges=edges, init_sub_graph={"a": 7.0})
    sm = {r["bizno"]: r["shock"] for r in res.shock_list}
    assert sm["b"] == pytest.approx(7.1795, rel=1e-3)
    assert res.converged is True
    assert len(res.damped_cycles) == 1
    dc = res.damped_cycles[0]
    assert set(dc["members"]) == {"b", "c", "d"}
    assert dc["rho"] == pytest.approx(1.0)
    assert dc["rho_after"] < 1.0

    # 대조: 반복 엔진은 같은 그래프에서 발산(미수렴)
    it = propagate_shock(edges=edges, init_sub_graph={"a": 7.0}, max_iter=200)
    assert it.converged is False


# ── 선형 체인 — 해석적 검증 ──────────────────────────────────────────────


def test_linear_chain_three_nodes() -> None:
    """A → B → C, rate=0.5 → effect = {A:1.0, B:0.5, C:0.25}.

    round 0: total={A:1}
    round 1: B += 1*0.5=0.5 → total={A:1, B:0.5}
    round 2: C += 0.5*0.5=0.25 → total={A:1, B:0.5, C:0.25}
    round 3: no outgoing from C → break (iterations=2)
    """
    edges = [_edge("A", "B", 0.5), _edge("B", "C", 0.5)]
    result = propagate_shock(edges=edges, init_sub_graph={"A": 1.0})

    sm = _shock_map(result)
    assert sm == pytest.approx({"A": 1.0, "B": 0.5, "C": 0.25})
    assert result.total_shock == pytest.approx(1.75)
    assert result.iterations == 2
    assert result.converged is True


def test_chain_rate_scales_proportionally() -> None:
    """rate 를 두 배 높이면 B·C 의 효과도 두 배."""
    def run(rate: float) -> dict[str, float]:
        return _shock_map(propagate_shock(
            edges=[_edge("A", "B", rate), _edge("B", "C", rate)],
            init_sub_graph={"A": 1.0},
        ))

    r1, r2 = run(0.3), run(0.6)
    assert r2["B"] == pytest.approx(r1["B"] * 2, rel=1e-9)
    assert r2["C"] == pytest.approx(r1["C"] * 4, rel=1e-9)  # rate² 비율


# ── 자기루프 — 기하급수 수렴 ─────────────────────────────────────────────


def test_self_loop_converges_to_geometric_series() -> None:
    """A → A, rate=0.5, init={A:1.0} → total_effect[A] = Σ 0.5^k = 1/(1-0.5) = 2.0."""
    result = propagate_shock(
        edges=[_edge("A", "A", 0.5)],
        init_sub_graph={"A": 1.0},
    )
    sm = _shock_map(result)
    # 기하급수 합 1/(1-r) = 2.0
    assert sm["A"] == pytest.approx(2.0, abs=1e-6)
    assert result.converged is True
    # 0.5^k < 1e-8 이 되는 k ≈ 27 → iterations ≤ 30
    assert result.iterations <= 30


def test_self_loop_different_rate() -> None:
    """rate=0.9 → 1/(1-0.9) = 10.0."""
    result = propagate_shock(
        edges=[_edge("A", "A", 0.9)],
        init_sub_graph={"A": 1.0},
    )
    sm = _shock_map(result)
    assert sm["A"] == pytest.approx(10.0, rel=1e-5)
    assert result.converged is True


def test_self_loop_with_downstream() -> None:
    """A → A (rate=0.5), A → B (rate=0.3), init={A:1.0}.

    A 의 total_effect 는 2.0 (자기루프 합).
    B 는 매 round A 의 current_shock 을 0.3 배 받으므로 Σ 0.5^k * 0.3 = 0.6.
    """
    result = propagate_shock(
        edges=[_edge("A", "A", 0.5), _edge("A", "B", 0.3)],
        init_sub_graph={"A": 1.0},
    )
    sm = _shock_map(result)
    assert sm["A"] == pytest.approx(2.0, abs=1e-6)
    # B += 1*0.3 + 0.5*0.3 + 0.25*0.3 + ... = 0.3 * (1/(1-0.5)) = 0.6
    assert sm["B"] == pytest.approx(0.6, abs=1e-6)


# ── 스타 토폴로지 ──────────────────────────────────────────────────────────


def test_star_topology_fan_out() -> None:
    """A → B, A → C, A → D (각 rate=0.3). B·C·D 에 outgoing 없음.

    total_effect = {A:1, B:0.3, C:0.3, D:0.3}, total_shock = 1.9
    """
    edges = [_edge("A", "B", 0.3), _edge("A", "C", 0.3), _edge("A", "D", 0.3)]
    result = propagate_shock(edges=edges, init_sub_graph={"A": 1.0})

    sm = _shock_map(result)
    assert sm == pytest.approx({"A": 1.0, "B": 0.3, "C": 0.3, "D": 0.3})
    assert result.total_shock == pytest.approx(1.9)
    assert result.iterations == 1


# ── 복수 시드 — 선형 중첩 원리 ────────────────────────────────────────────


def test_multi_seed_superposition() -> None:
    """두 독립 체인의 복수 시드 결과 = 개별 시드 결과의 합.

    체인: A→B→C, D→E (rate=0.5 공통)
    init={A:1.0, D:1.0} 결과는 init={A:1.0}+init={D:1.0} 결과의 합.
    """
    edges = [_edge("A", "B", 0.5), _edge("B", "C", 0.5), _edge("D", "E", 0.5)]

    combined = _shock_map(propagate_shock(edges=edges, init_sub_graph={"A": 1.0, "D": 1.0}))
    only_a   = _shock_map(propagate_shock(edges=edges, init_sub_graph={"A": 1.0}))
    only_d   = _shock_map(propagate_shock(edges=edges, init_sub_graph={"D": 1.0}))

    for node in ("A", "B", "C", "D", "E"):
        expected = only_a.get(node, 0.0) + only_d.get(node, 0.0)
        assert combined.get(node, 0.0) == pytest.approx(expected, abs=1e-9), node


def test_multi_seed_scale() -> None:
    """init 값을 k 배 하면 모든 노드의 shock 도 k 배."""
    edges = [_edge("A", "B", 0.4), _edge("B", "C", 0.6)]

    base = _shock_map(propagate_shock(edges=edges, init_sub_graph={"A": 1.0}))
    scaled = _shock_map(propagate_shock(edges=edges, init_sub_graph={"A": 3.0}))

    for node, val in base.items():
        assert scaled[node] == pytest.approx(val * 3.0, rel=1e-9)


# ── 안전장치 ──────────────────────────────────────────────────────────────


def test_max_iter_sets_converged_false() -> None:
    """rate=1.0 자기루프 → 신호가 줄어들지 않으므로 max_iter 도달."""
    result = propagate_shock(
        edges=[_edge("A", "A", 1.0)],
        init_sub_graph={"A": 1.0},
        max_iter=10,
    )
    assert result.converged is False
    assert result.iterations == 10


def test_epsilon_cutoff_stops_tiny_signals() -> None:
    """rate=0.5, init={A: 1e-9} — 초기 충격이 epsilon(1e-8) 이하면 즉시 정지."""
    result = propagate_shock(
        edges=[_edge("A", "B", 0.5)],
        init_sub_graph={"A": 1e-9},
        epsilon=1e-8,
    )
    # A 는 init 에서 total_effect 에 포함되지만 전파는 안 됨
    sm = _shock_map(result)
    assert "B" not in sm  # 전파 차단
    assert result.iterations == 0


def test_custom_epsilon_controls_precision() -> None:
    """epsilon 을 느슨하게 하면 더 일찍 종료 → iterations 감소."""
    edges = [_edge("A", "A", 0.5)]
    init = {"A": 1.0}

    tight = propagate_shock(edges=edges, init_sub_graph=init, epsilon=1e-10)
    loose = propagate_shock(edges=edges, init_sub_graph=init, epsilon=1e-4)

    assert loose.iterations < tight.iterations
    # 수렴값은 여전히 1/(1-0.5)=2.0 에 근사 (오차 허용 범위 내)
    assert _shock_map(tight)["A"] == pytest.approx(2.0, abs=1e-8)
    assert _shock_map(loose)["A"] == pytest.approx(2.0, abs=1e-2)


# ── ShockResult 인터페이스 ──────────────────────────────────────────────────


def test_total_shock_equals_sum_of_shock_list() -> None:
    """total_shock 은 항상 shock_list 의 shock 합계와 일치해야 한다."""
    edges = [_edge("A", "B", 0.7), _edge("B", "C", 0.4), _edge("A", "C", 0.2)]
    result = propagate_shock(edges=edges, init_sub_graph={"A": 1.0})

    manual_sum = sum(row["shock"] for row in result.shock_list)
    assert result.total_shock == pytest.approx(manual_sum, rel=1e-9)


def test_shock_list_contains_no_duplicates() -> None:
    """bizno 당 정확히 1개의 행만 있어야 한다."""
    edges = [_edge("A", "B", 0.5), _edge("A", "C", 0.3), _edge("B", "C", 0.2)]
    result = propagate_shock(edges=edges, init_sub_graph={"A": 1.0})

    biznos = [row["bizno"] for row in result.shock_list]
    assert len(biznos) == len(set(biznos))


def test_init_nodes_always_appear_in_result() -> None:
    """init_sub_graph 의 모든 bizno 는 edges 유무와 무관하게 결과에 포함된다."""
    result = propagate_shock(
        edges=[],
        init_sub_graph={"X": 10.0, "Y": 20.0},
    )
    sm = _shock_map(result)
    assert "X" in sm and "Y" in sm


# ── 수렴 보증 — 행 정규화 rate (Σ_out ≤ 1) ─────────────────────────────────


def test_row_normalized_rates_always_converge() -> None:
    """source 당 outgoing rate 합 ≤ 1 이면 반드시 수렴 (spectral radius ≤ 1).

    5-노드 완전 방향 그래프에서 각 source 의 rate 합 = 0.9 (< 1).
    """
    nodes = ["N0", "N1", "N2", "N3", "N4"]
    edges = []
    for src in nodes:
        others = [t for t in nodes if t != src]
        rate = 0.9 / len(others)  # 4 개 outgoing, 합 = 0.9
        for tgt in others:
            edges.append(_edge(src, tgt, rate))

    result = propagate_shock(edges=edges, init_sub_graph={n: 1.0 for n in nodes})
    assert result.converged is True
    assert math.isfinite(result.total_shock)
