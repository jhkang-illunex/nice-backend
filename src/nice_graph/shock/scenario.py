"""쇼크 시나리오 래퍼 — 관세 충격 / 거래 변화.

알고리즘(``propagate_shock``)·조립(``assemble_propagation_input``)은 그대로 두고,
"어떤 방향(상류/하류)으로, 어떤 비중 가중치(A/B)로, W 를 수정하느냐(g)" 의 **조합만**
묶는다. 신규 계산 엔진은 없음 — 전부 기존 모듈 재사용 (래퍼 한 겹).

방향 ↔ 라벨 ↔ 정규화  (★ 도메인 검증 지점)
  downstream : 셀러→바이어. 하류/**매입 파급**. 가중치 B.
               rate 정규화 = 셀러(source)의 총매출(outgoing) 대비.
  upstream   : 바이어→셀러. 상류/**매출 파급**. 가중치 A.
               rate 정규화 = 바이어(source)의 총매입(incoming) 대비.
  ※ 전파가 절대 수렴(Σ_out≤1)하려면 항상 "전파 source 의 outgoing 합" 으로 정규화해야
    한다. 그래서 상류(매출 파급)의 수학적 분모가 '바이어 총매입' 이 되는데, 이 명칭이
    경제적 의미와 어긋난다면 assemble 의 src_col 만 바꾸면 됨 (propagate 무변경).

시나리오
  tariff             : 그래프 W 불변. 시드(1차 기업)에 외생 충격만 주입.
                       요청 방향마다 assemble(direction, weight) + propagate 1회.
  transaction_change : 특정 1차→2차 엣지 비중에 g(0~1) 반영한 **수정 W**.
                       "변화분" = (수정 W 결과) − (원 W 결과) 의 노드별 Δ.
                       (difference-of-runs — baseline 1회 + changed 1회.)

호출 (모듈 3→2 권장 패턴과 동일하게 시드를 한 번에 묶어 단일 전파)
  >>> res = run_tariff_shock(primary_pairs, weight_a=0.8, weight_b=0.6)
  >>> for d in res.directions:           # 매출(상류)·매입(하류) 각각
  ...     print(d.effect_label, d.result.total_shock)
"""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from nice_graph.shock.assemble import (
    Direction,
    Normalize,
    PropagationInput,
    assemble_propagation_input,
    parse_node_id,
)
from nice_graph.shock.propagate import ShockResult, ShockRow, propagate_shock

log = logging.getLogger(__name__)

# 방향 → 사람이 읽는 파급 효과 라벨.
EFFECT_LABEL: dict[str, str] = {
    "upstream": "매출 파급",  # 상류, 가중치 A
    "downstream": "매입 파급",  # 하류, 가중치 B
}

DEFAULT_DIRECTIONS: tuple[Direction, ...] = ("upstream", "downstream")


# ── 결과 타입 ─────────────────────────────────────────────────────────────────


@dataclass
class DirectionResult:
    """한 방향(상류/하류)의 조립 입력 + 전파 결과."""

    direction: str  # "upstream" | "downstream"
    effect_label: str  # "매출 파급" | "매입 파급"
    weight: float  # 적용된 A 또는 B
    assembled: PropagationInput  # 디버그/결과출력용 (nodes 매핑 포함)
    result: ShockResult  # tariff=전파결과, transaction_change=Δ


@dataclass
class ScenarioResult:
    scenario: str  # "tariff" | "transaction_change"
    directions: list[DirectionResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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
    )


def _propagate(asm: PropagationInput, **propagate_kwargs) -> ShockResult:
    return propagate_shock(
        edges=asm.edges, init_sub_graph=asm.init_sub_graph, **propagate_kwargs
    )


def _delta(base: ShockResult, changed: ShockResult) -> ShockResult:
    """노드별 (changed − base) Δ 를 ShockResult 로. total/iterations 도 갱신."""
    bmap = {r["bizno"]: r["shock"] for r in base.shock_list}
    cmap = {r["bizno"]: r["shock"] for r in changed.shock_list}
    rows: list[ShockRow] = [
        ShockRow(bizno=k, shock=cmap.get(k, 0.0) - bmap.get(k, 0.0))
        for k in sorted(set(bmap) | set(cmap))
    ]
    return ShockResult(
        shock_list=rows,
        total_shock=float(sum(r["shock"] for r in rows)),
        iterations=max(base.iterations, changed.iterations),
        converged=base.converged and changed.converged,
    )


def _weight_for(direction: str, weight_a: float, weight_b: float) -> float:
    return weight_a if direction == "upstream" else weight_b


def _coerce_overrides(
    edge_overrides: Mapping[tuple[str, str], float] | None,
) -> dict[tuple[str, str], float]:
    """{(from,to): g} → {(str,str): float}. 빈 입력은 {}."""
    if not edge_overrides:
        return {}
    return {(str(k[0]), str(k[1])): float(v) for k, v in edge_overrides.items()}


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
    **propagate_kwargs,
) -> ScenarioResult:
    """관세 충격 — W 불변, 시드에 외생 충격만 주입. 요청 방향 각각 전파.

    Args:
      seeds: 1차 기업 (PrimarySelectionResult 또는 (bizno,upchecd) 쌍).
      weight_a/weight_b: 상류(매출)/하류(매입) 비중 가중치.
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
        )
        warnings.extend(f"[{d}] {m}" for m in asm.warnings)
        res = _propagate(asm, **propagate_kwargs)
        out.append(DirectionResult(d, EFFECT_LABEL[d], w, asm, res))
    log.info("tariff: directions=%s seeds_done", list(directions))
    return ScenarioResult("tariff", out, warnings)


def run_transaction_change(
    seeds,
    *,
    edge_overrides: Mapping[tuple[str, str], float],
    weight_a: float = 1.0,
    weight_b: float = 1.0,
    directions: Sequence[Direction] = DEFAULT_DIRECTIONS,
    depth: int = 3,
    trade_year: str | None = None,
    within_subgraph: bool = True,
    damping: float = 0.85,
    seed_shock=1.0,
    normalize: Normalize = "source",
    **propagate_kwargs,
) -> ScenarioResult:
    """거래 변화 — 특정 1차→2차 엣지 비중에 g(0~1) 반영한 수정 W. 변화분(Δ) 반환.

    각 방향마다 baseline(원 W) 1회 + changed(수정 W) 1회 전파 후 노드별 Δ.

    Args:
      edge_overrides: {(from_bizno, to_bizno): g} — 저장방향(셀러→바이어) 키.
      나머지: run_tariff_shock 와 동일.
    """
    ov = _coerce_overrides(edge_overrides)
    if not ov:
        raise ValueError("transaction_change 는 edge_overrides 가 비면 안 됨 (변화 대상 없음)")

    out: list[DirectionResult] = []
    warnings: list[str] = ["변화분 = 수정W − 원W (difference-of-runs)"]
    for d in directions:
        w = _weight_for(d, weight_a, weight_b)
        common = dict(
            direction=d,
            weight=w,
            depth=depth,
            trade_year=trade_year,
            within_subgraph=within_subgraph,
            damping=damping,
            seed_shock=seed_shock,
            normalize=normalize,
        )
        base_asm = _assemble_one(seeds, edge_overrides=None, **common)
        chg_asm = _assemble_one(seeds, edge_overrides=ov, **common)
        warnings.extend(f"[{d}] {m}" for m in chg_asm.warnings)
        base_res = _propagate(base_asm, **propagate_kwargs)
        chg_res = _propagate(chg_asm, **propagate_kwargs)
        out.append(DirectionResult(d, EFFECT_LABEL[d], w, chg_asm, _delta(base_res, chg_res)))
    log.info("transaction_change: directions=%s overrides=%d", list(directions), len(ov))
    return ScenarioResult("transaction_change", out, warnings)


# ── 1차↔2차 매출/매입 랜덤 가중치 생성 ─────────────────────────────────────────


@dataclass
class RandomOverrideSpec:
    """1차↔2차 거래에 부여할 랜덤 g 사양.

    side: 'both'(매출+매입) | 'sales'(매출=1차 판매→2차) | 'purchase'(매입=2차 판매→1차).
    low/high: g 범위 (0≤low≤high≤1). 수렴 보장 위해 상한 1.
    seed: 재현용 난수 시드 (None=비결정).
    only_firms: 일부 1차 기업 bizno 한정 (None=연계된 모든 1차).
    """

    side: str = "both"
    low: float = 0.0
    high: float = 1.0
    seed: int | None = None
    only_firms: tuple[str, ...] | None = None


def _seed_biznos(seeds) -> set[str]:
    firms = getattr(seeds, "firms", None)
    if firms is not None:  # PrimarySelectionResult
        return {f.bizno for f in firms if f.bizno}
    return {b for b, _ in seeds if b}


def build_primary_secondary_random_overrides(
    seeds,
    *,
    spec: RandomOverrideSpec,
    depth: int = 3,
    trade_year: str | None = None,
    within_subgraph: bool = True,
    damping: float = 0.85,
    seed_shock=1.0,
) -> dict[tuple[str, str], float]:
    """1차(시드)↔2차(직접 거래상대) 엣지의 매출/매입에 랜덤 g 를 부여한 edge_overrides 생성.

    분류 (company_edge 저장방향 = 셀러→바이어):
      매출   = 1차(셀러) → 2차(바이어)   : sb∈1차, bb∉1차
      매입   = 2차(셀러) → 1차(바이어)   : bb∈1차, sb∉1차
    1차↔1차 / 2차↔3차 등 (양끝 동시 1차이거나 1차 미포함) 은 제외 — 정확히 한쪽 끝만 1차.

    재현성: 후보 엣지를 정렬한 뒤 random.Random(spec.seed) 로 g 부여 (DB 행순서 무관).
    """
    if not (0.0 <= spec.low <= spec.high <= 1.0):
        raise ValueError(f"랜덤 범위는 0≤low≤high≤1 이어야 함: [{spec.low}, {spec.high}]")
    if spec.side not in ("both", "sales", "purchase"):
        raise ValueError(f"side 는 both|sales|purchase: {spec.side!r}")

    seed_biznos = _seed_biznos(seeds)
    target = seed_biznos & set(spec.only_firms) if spec.only_firms else seed_biznos

    # 1차↔2차 거래쌍 열거 — downstream 조립으로 저장방향 (셀러,바이어) 확보.
    dn = assemble_propagation_input(
        seeds,
        depth=depth,
        trade_year=trade_year,
        within_subgraph=within_subgraph,
        damping=damping,
        seed_shock=seed_shock,
        direction="downstream",
    )
    want_sales = spec.side in ("both", "sales")
    want_purchase = spec.side in ("both", "purchase")

    cands: list[tuple[str, str]] = []
    for e in dn.edges:
        sb, _ = parse_node_id(e["from_bizno"])
        bb, _ = parse_node_id(e["to_bizno"])
        is_sales = sb in target and bb not in seed_biznos  # 1차 판매 → 2차
        is_purchase = bb in target and sb not in seed_biznos  # 2차 판매 → 1차(=1차 매입)
        if (want_sales and is_sales) or (want_purchase and is_purchase):
            cands.append((sb, bb))

    cands = sorted(set(cands))  # 결정적 순서 (seed 재현성)
    rng = random.Random(spec.seed)
    ov = {pair: round(rng.uniform(spec.low, spec.high), 4) for pair in cands}
    log.info(
        "random overrides: 1차↔2차 side=%s firms=%d 엣지=%d seed=%s",
        spec.side, len(target), len(ov), spec.seed,
    )
    return ov
