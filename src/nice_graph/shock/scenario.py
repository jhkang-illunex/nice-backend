"""쇼크 시나리오 래퍼 — 관세 충격 / 거래 변화.

알고리즘(``propagate_shock``)·조립(``assemble_propagation_input``)은 그대로 두고,
"어떤 방향(상류/하류)으로, 어떤 비중 가중치(A/B)로, W 를 수정하느냐(g)" 의 **조합만**
묶는다. 신규 계산 엔진은 없음 — 전부 기존 모듈 재사용 (래퍼 한 겹).

방향 ↔ 라벨 ↔ 정규화  (★ 문서 기획안 기준, 2026-06-19 확정)
  downstream : 셀러→바이어. 하류/**매출 파급**(1차 기업의 매출처=고객 방향). 가중치 A.
               rate 정규화 = 셀러(source)의 총매출(outgoing) 대비.
  upstream   : 바이어→셀러. 상류/**매입 파급**(1차 기업의 매입처=공급사 방향). 가중치 B.
               rate 정규화 = 바이어(source)의 총매입(incoming) 대비.
  ※ 전파가 절대 수렴(Σ_out≤1)하려면 항상 "전파 source 의 outgoing 합" 으로 정규화한다.
    direction(orientation)·normalize 는 엔진 그대로이고, 라벨(매출/매입)과 가중치 A/B 의
    바인딩만 문서에 맞췄다(매출 파급=downstream/A, 매입 파급=upstream/B).

시나리오
  tariff             : 그래프 W 불변. 시드(1차 기업)에 외생 충격만 주입.
                       요청 방향마다 assemble(direction, weight) + propagate 1회.
  transaction_change : 특정 1차→2차 거래의 **매입/매출 비중**(엣지 rate)에 g(0~1) 가중치를
                       곱해 거래 내역을 바꾼 **수정 W**(=W′). 즉 "거래 내역 변화".
                       "변화분"(명세) = 거래 내역이 바뀐 순효과 = (수정 W′ 전파) − (원 W 전파)
                       의 노드별 Δ → difference-of-runs(baseline 1회 + changed 1회)로 산출.
                       (확정: 명세 "변화분을 seed로" = 별도 델타주입이 아니라 '결과로 나오는
                        값이 곧 변화분(Δ)' 의 의미. 2026-06-19 사용자 확정.)

호출 (모듈 3→2 권장 패턴과 동일하게 시드를 한 번에 묶어 단일 전파)
  >>> res = run_tariff_shock(primary_pairs, weight_a=0.8, weight_b=0.6)
  >>> for d in res.directions:           # 매출(상류)·매입(하류) 각각
  ...     print(d.effect_label, d.result.total_shock)

호출자 (caller) 체계 — 누가 어떤 함수를 호출 가능한가
  ┌─ run_scenario ────────────── 단일 디스패치(권장 진입점)
  │     호출자: ① API 라우터  api/routers/shock.py  `scenario()` (별칭 _run_scenario)
  │            ② Streamlit 데모 nice_demo/app_shock.py `step_scenario()`
  │            ③ 라이브러리 직접 사용(테스트·스크립트)
  │     내부에서 시나리오에 따라 ↓ 둘 중 하나로 분기:
  ├─ run_tariff_shock ────────── 관세 충격(W불변)
  │     호출자: run_scenario(권장) · 라이브러리 직접 · 테스트.
  │             (라우터·데모는 run_scenario 를 통해 간접 호출 — 직접 호출 안 함)
  ├─ run_transaction_change ──── 거래 변화(수정 W·Δ)
  │     호출자: run_scenario(권장) · 라이브러리 직접 · 테스트.
  ├─ build_primary_secondary_random_overrides ── 1차↔2차 랜덤 g 생성
  │     호출자: run_scenario(random_spec 경로 내부) · 데모 `_override_random` ·
  │             라이브러리 직접.
  └─ enumerate_primary_secondary ── 1차↔2차 거래쌍 열거(매출/매입 분류, 공유 경로)
        호출자: build_primary_secondary_random_overrides(내부) ·
                데모 `_override_manual`(수동 편집기 후보 목록) · 라이브러리 직접.

  요약: 외부(API/데모)는 **run_scenario 단일 진입점**으로 들어오고, 그 아래
  run_tariff_shock / run_transaction_change 는 run_scenario 가 호출하는 것을 권장한다
  (직접 호출도 가능하나 라우터·데모는 일관성을 위해 run_scenario 만 사용).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from nice_graph.shock.assemble import (
    Direction,
    Normalize,
    PropagationInput,
    assemble_propagation_input,
    parse_node_id,
    run_propagation,
)
from nice_graph.shock.propagate import ShockResult, ShockRow

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
    applied_overrides: dict[tuple[str, str], float] = field(default_factory=dict)
    # 실제 적용된 거래변화 g (랜덤 생성 포함). tariff 면 빈 dict.


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
    # 매출 파급(downstream)=가중치 A, 매입 파급(upstream)=가중치 B (문서 기준).
    return weight_a if direction == "downstream" else weight_b


def _apply_overrides(
    asm: PropagationInput, overrides: Mapping[tuple[str, str], float]
) -> list[dict]:
    """조립된 baseline 엣지에 g 를 in-memory 로 적용 — assemble 의 edge_overrides 와 동치.

    assemble 은 rate = W·α·(amt/denom)·g 로 빌드하므로 baseline(g=1) 대비 수정 rate = rate·g.
    이를 활용해 **수정 W 를 위한 2차 DB 조립을 생략**한다(같은 서브그래프 재조회 제거).

    override 키 = 저장방향 (셀러, 바이어). 조립 엣지는 방향에 따라 oriented nid 라
    asm.direction 으로 (셀러,바이어) 를 복원해 매칭. rate≤0 은 assemble 과 동일하게 제외.
    """
    if not overrides:
        return list(asm.edges)
    out: list[dict] = []
    for e in asm.edges:
        fb, _ = parse_node_id(e["from_bizno"])
        tb, _ = parse_node_id(e["to_bizno"])
        seller, buyer = (fb, tb) if asm.direction == "downstream" else (tb, fb)
        g = float(overrides.get((seller, buyer), 1.0))
        new_rate = float(e["rate"]) * g
        if new_rate <= 0:
            continue
        out.append(e if g == 1.0 else {**e, "rate": new_rate})
    return out


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

    호출자: run_scenario(권장 진입점) · 라이브러리 직접 · 테스트.
            (API 라우터·데모는 run_scenario 를 통해 간접 호출)

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
        res = run_propagation(asm, **propagate_kwargs)
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

    호출자: run_scenario(권장 진입점) · 라이브러리 직접 · 테스트.
            (API 라우터·데모는 run_scenario 를 통해 간접 호출)

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
    if all(abs(g - 1.0) < 1e-12 for g in ov.values()):
        warnings.append("모든 override g=1.0 — 변화 없음(Δ=0). factor<1.0 로 변경 대상을 지정하세요.")
    for d in directions:
        w = _weight_for(d, weight_a, weight_b)
        # 방향별 baseline 만 DB 조립(1회). 수정 W 는 in-memory g 적용(2차 조립 생략).
        base_asm = _assemble_one(
            seeds,
            edge_overrides=None,
            direction=d,
            weight=w,
            depth=depth,
            trade_year=trade_year,
            within_subgraph=within_subgraph,
            damping=damping,
            seed_shock=seed_shock,
            normalize=normalize,
        )
        warnings.extend(f"[{d}] {m}" for m in base_asm.warnings)
        chg_asm = replace(base_asm, edges=_apply_overrides(base_asm, ov))
        base_res = run_propagation(base_asm, **propagate_kwargs)
        chg_res = run_propagation(chg_asm, **propagate_kwargs)
        out.append(DirectionResult(d, EFFECT_LABEL[d], w, chg_asm, _delta(base_res, chg_res)))
    log.info(
        "transaction_change: directions=%s overrides=%d (baseline 1회/방향)",
        list(directions), len(ov),
    )
    return ScenarioResult("transaction_change", out, warnings, applied_overrides=dict(ov))


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


@dataclass
class PrimarySecondaryEdge:
    """1차↔2차 거래 엣지(저장방향 셀러→바이어) + 매출/매입 분류·표시 정보."""

    from_bizno: str  # 셀러
    to_bizno: str  # 바이어
    side: str  # "sales"(1차→2차) | "purchase"(2차→1차)
    rate: float  # downstream baseline rate (표시·참고용)
    from_name: str | None
    to_name: str | None


def enumerate_primary_secondary(
    seeds,
    *,
    side: str = "both",
    only_firms: tuple[str, ...] | None = None,
    depth: int = 3,
    trade_year: str | None = None,
    within_subgraph: bool = True,
    damping: float = 0.85,
    seed_shock=1.0,
) -> list[PrimarySecondaryEdge]:
    """1차(시드)↔2차(직접 거래상대) 엣지를 매출/매입으로 분류해 열거 (단일 공유 경로).

    호출자: build_primary_secondary_random_overrides(내부) ·
            데모 _override_manual(수동 편집기 후보 목록) · 라이브러리 직접.

    분류 (company_edge 저장방향 = 셀러→바이어):
      매출(sales)    = 1차(셀러) → 2차(바이어)  : sb∈1차, bb∉1차
      매입(purchase) = 2차(셀러) → 1차(바이어)  : bb∈1차, sb∉1차
    1차↔1차 / 2차↔3차 (양끝 동시 1차이거나 1차 미포함) 제외 — 정확히 한쪽 끝만 1차.
    downstream 조립으로 저장방향·baseline rate·기업명 확보. 랜덤 생성기와 데모 수동
    편집기가 이 한 경로를 공유한다.
    """
    if side not in ("both", "sales", "purchase"):
        raise ValueError(f"side 는 both|sales|purchase: {side!r}")
    seed_biznos = _seed_biznos(seeds)
    target = seed_biznos & set(only_firms) if only_firms else seed_biznos
    if only_firms and not target:
        raise ValueError(
            f"only_firms 가 시드 기업과 교집합 없음 — only_firms={list(only_firms)[:5]} "
            f"vs seeds={sorted(seed_biznos)[:5]}"
        )
    dn = assemble_propagation_input(
        seeds,
        depth=depth,
        trade_year=trade_year,
        within_subgraph=within_subgraph,
        damping=damping,
        seed_shock=seed_shock,
        direction="downstream",
    )
    idx = dn.node_index()
    want_sales = side in ("both", "sales")
    want_purchase = side in ("both", "purchase")
    out: list[PrimarySecondaryEdge] = []
    for e in dn.edges:
        sb, _ = parse_node_id(e["from_bizno"])
        bb, _ = parse_node_id(e["to_bizno"])
        is_sales = sb in target and bb not in seed_biznos
        is_purchase = bb in target and sb not in seed_biznos
        if want_sales and is_sales:
            s = "sales"
        elif want_purchase and is_purchase:
            s = "purchase"
        else:
            continue
        fn, tn = idx.get(e["from_bizno"]), idx.get(e["to_bizno"])
        out.append(
            PrimarySecondaryEdge(
                sb, bb, s, float(e["rate"]),
                fn.korentrnm if fn else None, tn.korentrnm if tn else None,
            )
        )
    return out


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
    """1차↔2차 매출/매입 엣지에 랜덤 g 를 부여한 edge_overrides 생성.

    호출자: run_scenario(random_spec 경로 내부) · 데모 _override_random · 라이브러리 직접.

    enumerate_primary_secondary 후보를 정렬해 random.Random(spec.seed) 로 g 부여
    (DB 행순서 무관·재현 보장).
    """
    if not (0.0 <= spec.low <= spec.high <= 1.0):
        raise ValueError(f"랜덤 범위는 0≤low≤high≤1 이어야 함: [{spec.low}, {spec.high}]")
    edges = enumerate_primary_secondary(
        seeds,
        side=spec.side,
        only_firms=spec.only_firms,
        depth=depth,
        trade_year=trade_year,
        within_subgraph=within_subgraph,
        damping=damping,
        seed_shock=seed_shock,
    )
    pairs = sorted({(e.from_bizno, e.to_bizno) for e in edges})
    rng = random.Random(spec.seed)
    ov = {pair: round(rng.uniform(spec.low, spec.high), 4) for pair in pairs}
    log.info("random overrides: 1차↔2차 side=%s 엣지=%d seed=%s", spec.side, len(ov), spec.seed)
    return ov


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
    edge_overrides: Mapping[tuple[str, str], float] | None = None,
    random_spec: RandomOverrideSpec | None = None,
    **propagate_kwargs,
) -> ScenarioResult:
    """시나리오 단일 디스패치 — 라우터·데모가 공유하는 진입점.

    호출자: ① API 라우터 api/routers/shock.py `scenario()` (별칭 _run_scenario)
            ② Streamlit 데모 nice_demo/app_shock.py `step_scenario()`
            ③ 라이브러리 직접(테스트·스크립트).
    외부 진입은 이 함수로 일원화 — 내부에서 tariff/transaction_change 로 분기한다.

    tariff             : run_tariff_shock.
    transaction_change : random_spec 있으면 1차↔2차 랜덤 g 생성, 없으면 edge_overrides 사용.
    결과 ``.applied_overrides`` 로 실제 적용된 g 노출.
    """
    common = dict(
        weight_a=weight_a,
        weight_b=weight_b,
        directions=directions,
        depth=depth,
        trade_year=trade_year,
        within_subgraph=within_subgraph,
        damping=damping,
        normalize=normalize,
        seed_shock=seed_shock,
    )
    if scenario == "tariff":
        return run_tariff_shock(seeds, **common, **propagate_kwargs)
    if scenario != "transaction_change":
        raise ValueError(f"scenario 는 tariff|transaction_change: {scenario!r}")
    if random_spec is not None:
        ov: dict = build_primary_secondary_random_overrides(
            seeds,
            spec=random_spec,
            depth=depth,
            trade_year=trade_year,
            within_subgraph=within_subgraph,
            damping=damping,
            seed_shock=seed_shock,
        )
    else:
        ov = dict(edge_overrides or {})
    return run_transaction_change(seeds, edge_overrides=ov, **common, **propagate_kwargs)
