"""쇼크 시나리오 래퍼 — 외생충격(tariff) / 거래량 변동(volume).

알고리즘(``propagate_shock``)·조립(``assemble_propagation_input``)은 그대로 두고,
"어떤 방향(상류/하류)으로, 어떤 비중 가중치(A/B)로, 무엇을 주입하느냐" 의 조합만 묶는다.

방향 ↔ 라벨  (★ 문서 기획안 기준, 2026-06-19 확정)
  downstream : 셀러→바이어. 하류/**매출 파급**(매출처=고객 방향). 가중치 A.
  upstream   : 바이어→셀러. 상류/**매입 파급**(매입처=공급사 방향). 가중치 B.

시나리오 (2종)
  tariff : 외생충격. W 불변, 시드에 외생 충격(seed_shock)을 주입해 절대 파급량 산출.
  volume : 거래량 변동. W 불변, 변동을 **편차 δ=m−1 로 주입**해 1회 전파 후 shock=1+δ전파,
           각 노드의 매출/매입에 ×반영. 입력 = firm_specs(1차 매출/매입, 2차 미명시=전체)
           · multipliers(기업 전체) · edge_multipliers(특정 거래). difference-of-runs(구
           transaction_change)는 폐기되고 volume 으로 일원화(2026-06-22).

진입점: run_scenario(scenario, ...) — API 라우터·데모·라이브러리 공통.
  내부에서 tariff → run_tariff_shock, volume → run_volume_shock 로 분기.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from nice_graph.shock.assemble import (
    Direction,
    Normalize,
    PropagationInput,
    assemble_propagation_input,
    edge_in_shares,
    run_propagation,
)
from nice_graph.shock.propagate import ShockResult, propagate_dispatch

log = logging.getLogger(__name__)

# 방향 → 사람이 읽는 파급 효과 라벨 (★ 문서 기획안 기준, 2026-06-19 확정).
#   매출 파급 = 1차 기업의 '매출처(고객)' 방향 = 셀러→바이어 전파 = downstream, 가중치 A.
#   매입 파급 = 1차 기업의 '매입처(공급사)' 방향 = 바이어→셀러 전파 = upstream,  가중치 B.
# (엔진의 direction(orientation) 은 그대로 — 라벨·가중치 바인딩만 문서에 맞춤.)
EFFECT_LABEL: dict[str, str] = {
    "downstream": "매출 파급",  # 매출처(고객)/하류, 가중치 A
    "upstream": "매입 파급",  # 매입처(공급사)/상류, 가중치 B
}

DEFAULT_DIRECTIONS: tuple[Direction, ...] = ("upstream", "downstream")

# 거래 변화 g(factor) 상한. g=1+증감율: 0.8=20%감소, 1.1=10%증가, 1.0=무변화.
#   g<1=감소(항상 수렴), g>1=증가(Σ_out 가 1 을 넘으면 수렴 보장 깨짐 → 경고).
MAX_OVERRIDE_FACTOR = 3.0


def _max_source_outsum(edges: list[dict]) -> float:
    """엣지에서 source 별 Σ_out(rate 합) 최댓값. >1 이면 ρ(R)>1 발산 위험."""
    acc: dict[str, float] = {}
    for e in edges:
        acc[e["from_bizno"]] = acc.get(e["from_bizno"], 0.0) + float(e["rate"])
    return max(acc.values(), default=0.0)


# ── 결과 타입 ─────────────────────────────────────────────────────────────────


@dataclass
class DirectionResult:
    """한 방향(상류/하류)의 조립 입력 + 전파 결과."""

    direction: str  # "upstream" | "downstream"
    effect_label: str  # "매출 파급" | "매입 파급"
    weight: float  # 적용된 A 또는 B
    assembled: PropagationInput  # 디버그/결과출력용 (nodes 매핑 포함)
    result: ShockResult  # tariff=절대 파급, volume=shock(=1+δ전파)


@dataclass
class ScenarioResult:
    scenario: str  # "tariff" | "volume"
    directions: list[DirectionResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    applied_overrides: dict[tuple[str, str], float] = field(default_factory=dict)
    # (구) 거래변화 적용 g. volume/tariff 에선 빈 dict (호환 보존).


# ── 내부 ──────────────────────────────────────────────────────────────────────


def _assemble_one(
    seeds,
    *,
    direction: Direction,
    weight: float,
    depth: int,
    trade_year: str | None,
    within_subgraph: bool,
    damping: float,
    seed_shock,
    edge_overrides: Mapping[tuple[str, str], float] | None,
    normalize: Normalize = "source",
    industry_code=None,
) -> PropagationInput:
    return assemble_propagation_input(
        seeds,
        depth=depth,
        trade_year=trade_year,
        within_subgraph=within_subgraph,
        damping=damping,
        seed_shock=seed_shock,
        direction=direction,
        direction_weight=weight,
        normalize=normalize,
        edge_overrides=edge_overrides,
        industry_code=industry_code,
    )


def _weight_for(direction: str, weight_a: float, weight_b: float) -> float:
    # 매출 파급(downstream)=가중치 A, 매입 파급(upstream)=가중치 B (문서 기준).
    return weight_a if direction == "downstream" else weight_b


# ── public API ────────────────────────────────────────────────────────────────


def run_tariff_shock(
    seeds,
    *,
    weight_a: float = 1.0,
    weight_b: float = 1.0,
    directions: Sequence[Direction] = DEFAULT_DIRECTIONS,
    depth: int = 3,
    trade_year: str | None = None,
    within_subgraph: bool = True,
    damping: float = 0.85,
    seed_shock=1.0,
    normalize: Normalize = "source",
    industry_code=None,
    **propagate_kwargs,
) -> ScenarioResult:
    """관세 충격 — W 불변, 시드에 외생 충격만 주입. 요청 방향 각각 전파.

    호출자: run_scenario(권장 진입점) · 라이브러리 직접 · 테스트.
            (API 라우터·데모는 run_scenario 를 통해 간접 호출)

    Args:
      seeds: 1차 기업 (PrimarySelectionResult 또는 (bizno,upchecd) 쌍).
      weight_a/weight_b: 하류(매출 파급, downstream)/상류(매입 파급, upstream) 비중 가중치.
      directions: 계산할 방향 (기본 둘 다 → 매출·매입 파급 동시 반환).
      normalize: rate 분모 기준 source(수렴보장) | counterparty(매출/매입 비중 라벨).
      그 외: assemble_propagation_input 로 그대로 전달.
    """
    out: list[DirectionResult] = []
    warnings: list[str] = []
    for d in directions:
        w = _weight_for(d, weight_a, weight_b)
        asm = _assemble_one(
            seeds,
            direction=d,
            weight=w,
            depth=depth,
            trade_year=trade_year,
            within_subgraph=within_subgraph,
            damping=damping,
            seed_shock=seed_shock,
            edge_overrides=None,
            normalize=normalize,
            industry_code=industry_code,
        )
        warnings.extend(f"[{d}] {m}" for m in asm.warnings)
        res = run_propagation(asm, **propagate_kwargs)
        out.append(DirectionResult(d, EFFECT_LABEL[d], w, asm, res))
    log.info("tariff: directions=%s seeds_done", list(directions))
    return ScenarioResult("tariff", out, warnings)


# ── 볼륨 충격 (거래량 변동 v2) — W 불변·편차 전파·매출/매입 반영 ─────────────────


@dataclass
class VolumeSpec:
    """거래량 변동 입력 1건 — 1차 기업의 매출/매입 거래가 factor 배.

    bizno  : 1차(시드) 기업.
    side   : 'sales'(매출=1차→2차) | 'purchase'(매입=2차→1차).
    factor : g = 1+증감율 (0.8=20%감소, 1.1=10%증가).
    partner: 2차 bizno. None 이면 **1차의 그 side 모든 거래처**에 적용.
    """

    bizno: str
    side: str
    factor: float
    partner: str | None = None


# side ↔ 전파 방향: 매출 변동=하류(downstream), 매입 변동=상류(upstream).
_SIDE_DIRECTION = {"sales": "downstream", "purchase": "upstream"}


def _firm_specs_delta(
    specs: Sequence[VolumeSpec], side: str, trade_year, biz2nid: dict[str, str]
) -> dict[str, float]:
    """해당 side 의 VolumeSpec 들을 상대(2차) 노드 δ 로 환산 (거래 비중 가중)."""
    from nice_graph.shock.assemble import firm_partner_shares

    delta: dict[str, float] = {}
    for sp in specs:
        if sp.side != side:
            continue
        shares = firm_partner_shares(sp.bizno, side, trade_year, partner=sp.partner)
        for partner_b, share in shares.items():
            nid = biz2nid.get(partner_b)
            if nid is None:
                continue
            delta[nid] = delta.get(nid, 0.0) + share * (float(sp.factor) - 1.0)
    return delta


def _volume_shock_result(asm, init_delta, pin_ids, **propagate_kwargs):
    """init_delta(편차) 로 1회 전파 → shock=1+propagated. pin_ids 노드는 incoming 차단."""
    prop_asm = replace(
        asm, edges=[e for e in asm.edges if e["to_bizno"] not in pin_ids]
    ) if pin_ids else asm
    res = propagate_dispatch(
        edges=prop_asm.edges, init_sub_graph=init_delta, **propagate_kwargs
    )
    return ShockResult(
        shock_list=[{"bizno": r["bizno"], "shock": 1.0 + r["shock"]} for r in res.shock_list],
        total_shock=res.total_shock,
        iterations=res.iterations,
        converged=res.converged,
        damped_cycles=res.damped_cycles,
    )


def run_volume_shock(
    seeds,
    *,
    firm_specs: Sequence[VolumeSpec] | None = None,
    multipliers: Mapping[str, float] | None = None,
    edge_multipliers: Mapping[tuple[str, str], float] | None = None,
    directions: Sequence[Direction] = DEFAULT_DIRECTIONS,
    weight_a: float = 1.0,
    weight_b: float = 1.0,
    depth: int = 3,
    trade_year: str | None = None,
    within_subgraph: bool = True,
    damping: float = 0.85,
    normalize: Normalize = "source",
    pin_seeds: bool = True,
    industry_code=None,
    **propagate_kwargs,
) -> ScenarioResult:
    """거래량 변동 — 특정 기업의 매출/매입이 m배(1=중립) 변할 때 연결 기업 영향.

    "쿠팡 매출 −20% → 연결 기업 매출 몇 % 변동?" 류 시나리오. W(거래 비중)는 불변,
    충격을 **편차 δ=m−1 로 시드에 주입**해 한 번 전파한 뒤 1 을 더한다:
        shock[node] = 1 + Σ_k Wᵏ·δ           (δ=0 인 노드는 정확히 1=무변화)
        조정 매출/매입 = shock[node] × 기준액   (변동율 = shock−1)

    difference-of-runs(W 수정·2회·차분) 와 별개 루틴. 직접 입력(실측 증감율)·1회 전파.

    호출자: run_scenario(scenario='volume') · 데모 · 라이브러리/테스트.

    두 가지 입력 단위(택1 또는 병용):
      multipliers      : {bizno: m} — **기업 전체** 매출/매입이 m배. δ_seed = m−1 시드 주입.
      edge_multipliers : {(from,to): g} — **특정 거래(엣지)** 만 g배. 파트너(to) 노드에
                         δ += share(from→to)·(g−1) 주입. share = 그 거래가 to 의 총매입에서
                         차지하는 비중(거래 비중 가중) → 거래분만큼만 반영. 쿠팡 자신은 δ=0.

    Args:
      seeds: 시드/허브 기업 (bizno,upchecd). 서브그래프 확장 기준.
      directions: 전파 방향. 매출 감소→공급사 영향은 upstream(매입 파급) 축.
      pin_seeds: True(기본,B)=주입 노드를 입력값에 고정(되돌이 incoming 차단). False(A)=피드백 허용.
    """
    multipliers = multipliers or {}
    edge_multipliers = edge_multipliers or {}
    firm_specs = list(firm_specs or [])
    seed_biznos = _seed_biznos(seeds)
    node_delta = {b: float(multipliers.get(b, 1.0)) - 1.0 for b in seed_biznos}
    shares = edge_in_shares(list(edge_multipliers), trade_year) if edge_multipliers else {}
    # firm_specs 있으면 방향을 side 로부터 도출(매출→하류, 매입→상류).
    if firm_specs:
        directions = tuple(
            dict.fromkeys(_SIDE_DIRECTION[sp.side] for sp in firm_specs)
        )
    out: list[DirectionResult] = []
    warnings: list[str] = [
        "거래량 변동(volume): shock=1+Σ_k Wᵏ·δ (δ=m−1 또는 share·(g−1)), 변동율=shock−1, 조정액=shock×기준액."
    ]
    if not any(abs(v) > 1e-12 for v in node_delta.values()) and not edge_multipliers \
            and not firm_specs:
        warnings.append("변동 입력 없음(δ=0). firm_specs/multipliers/edge_multipliers 지정 필요.")
    for d in directions:
        w = _weight_for(d, weight_a, weight_b)
        asm = _assemble_one(
            seeds,
            direction=d,
            weight=w,
            depth=depth,
            trade_year=trade_year,
            within_subgraph=within_subgraph,
            damping=damping,
            seed_shock=node_delta,  # init = δ_seed (편차), 미주입 노드는 0
            edge_overrides=None,  # W 불변
            normalize=normalize,
            industry_code=industry_code,
        )
        warnings.extend(f"[{d}] {m}" for m in asm.warnings)
        # init = 시드 δ 에 엣지/firm_specs δ(파트너 노드) 를 덧씌움
        init = dict(asm.init_sub_graph)
        biz2nid = {n.bizno: n.node_id for n in asm.nodes}
        for (f, t), g in edge_multipliers.items():
            nid_t = biz2nid.get(t)
            if nid_t is None:
                warnings.append(f"[{d}] edge ({f}→{t}) 의 to 가 서브그래프 밖 — 무시")
                continue
            init[nid_t] = init.get(nid_t, 0.0) + shares.get((f, t), 0.0) * (float(g) - 1.0)
        # firm_specs — 이 방향(side)에 해당하는 1차 거래처별 δ (거래 비중 가중)
        side = "sales" if d == "downstream" else "purchase"
        for nid, dv in _firm_specs_delta(firm_specs, side, trade_year, biz2nid).items():
            init[nid] = init.get(nid, 0.0) + dv
        pin_ids = set(init) if pin_seeds else set()
        shocked = _volume_shock_result(asm, init, pin_ids, **propagate_kwargs)
        out.append(DirectionResult(d, EFFECT_LABEL[d], w, asm, shocked))
    log.info(
        "volume: dirs=%s seeds=%d node_mul=%d edge_mul=%d",
        list(directions), len(seed_biznos), len(multipliers), len(edge_multipliers),
    )
    return ScenarioResult("volume", out, warnings)


def _seed_biznos(seeds) -> set[str]:
    firms = getattr(seeds, "firms", None)
    if firms is not None:  # PrimarySelectionResult
        return {f.bizno for f in firms if f.bizno}
    return {b for b, _ in seeds if b}


# ── 단일 디스패치 (라우터·데모 공통 진입점) ────────────────────────────────────


def run_scenario(
    scenario: str,
    seeds,
    *,
    weight_a: float = 1.0,
    weight_b: float = 1.0,
    directions: Sequence[Direction] = DEFAULT_DIRECTIONS,
    depth: int = 3,
    trade_year: str | None = None,
    within_subgraph: bool = True,
    damping: float = 0.85,
    normalize: Normalize = "source",
    seed_shock=1.0,
    firm_specs: Sequence[VolumeSpec] | None = None,
    multipliers: Mapping[str, float] | None = None,
    edge_multipliers: Mapping[tuple[str, str], float] | None = None,
    pin_seeds: bool = True,
    industry_code=None,
    **propagate_kwargs,
) -> ScenarioResult:
    """시나리오 단일 디스패치 — 라우터·데모가 공유하는 진입점.

    호출자: ① API 라우터 api/routers/shock.py `scenario()` (별칭 _run_scenario)
            ② Streamlit 데모 nice_demo/app_shock.py `step_scenario()`
            ③ 라이브러리 직접(테스트·스크립트).
    외부 진입은 이 함수로 일원화 — 내부에서 tariff/volume 로 분기.

    tariff : run_tariff_shock — 외생충격(W 불변·시드 외생주입).
    volume : run_volume_shock — 거래량 변동(W 불변·편차 전파·매출/매입 반영).
             firm_specs(권장)·multipliers(기업전체)·edge_multipliers(저수준), pin_seeds.
    """
    if scenario == "volume":
        return run_volume_shock(
            seeds,
            firm_specs=firm_specs,
            multipliers=multipliers or {},
            edge_multipliers=edge_multipliers or {},
            directions=directions,
            weight_a=weight_a,
            weight_b=weight_b,
            depth=depth,
            trade_year=trade_year,
            within_subgraph=within_subgraph,
            damping=damping,
            normalize=normalize,
            pin_seeds=pin_seeds,
            industry_code=industry_code,
            **propagate_kwargs,
        )
    if scenario != "tariff":
        raise ValueError(f"scenario 는 tariff|volume: {scenario!r}")
    return run_tariff_shock(
        seeds,
        weight_a=weight_a,
        weight_b=weight_b,
        directions=directions,
        depth=depth,
        trade_year=trade_year,
        within_subgraph=within_subgraph,
        damping=damping,
        normalize=normalize,
        seed_shock=seed_shock,
        industry_code=industry_code,
        **propagate_kwargs,
    )
