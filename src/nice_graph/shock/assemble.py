"""2안 — 1차 시드 → 3-depth 거래확장 → 전파 모델 입력 조립.

역할
  ``screen.select_primary_firms`` 가 뽑은 1차 기업((bizno, upchecd) 쌍)을 시드로,
  ``company_edge`` 를 N-depth 확장해 ``propagate_shock`` 이 그대로 받는 입력
  (edges + init_sub_graph) 으로 조립한다. 즉 *전파 모델 호출용 데이터 어셈블러*.

개별 기업 = 복합키 (bizno, upchecd)  ← 과제 제공측 정의
  거래 테이블 ``company_edge`` 는 ``from_bizno``/``to_bizno`` 만 갖고 upchecd 가
  없다(노드 입도가 bizno). 그래서 어셈블러는 양끝 bizno 에 ``company`` 로
  upchecd 를 주입해 **복합키 노드 id = f"{bizno}|{upchecd}"** 로 승격하고,
  전파 결과도 (bizno, upchecd) 단위로 되돌린다. 현재 데이터는 bizno↔upchecd
  가 1:1 이라 승격이 무손실이지만, 한 bizno 가 복수 upchecd(다중 사업장)를
  가지면 엣지가 모호해지므로 그 경우를 ``warnings`` 로 표면화한다.

정규화 (수렴 보장)
  rate = sly_amt(from→to) / Σ_out(from)  — source 의 outgoing 행 정규화.
  ``within_subgraph=True`` (default): 분모를 *서브그래프 안* outgoing 합으로
    (Σ_out = 1, 기존 fetch_subgraph 의 all_rate 와 동일 의미).
  ``within_subgraph=False``: 분모를 source 의 *전체* outgoing 합으로 (경계
    밖으로 충격이 새는 leakage 모델, Σ_out ≤ 1 → 더 보수적·현실적).
  둘 다 spectral radius ρ(R) ≤ 1 이라 propagate_shock 의 거듭제곱급수가 수렴.

연도
  trade_year=None  : 전 연도 sly_amt 합으로 rate (all_rate).
  trade_year='2024': 그 연도 거래만으로 rate (years_rate 개념).
  주의 — company_edge 는 2024·2026 만 존재(2025 없음). screen 의 씨앗 선정
  연도와 전파 거래 연도는 별개 테이블이라 커버리지가 다를 수 있다.

출력 (PropagationInput)
  edges          : [{'from_bizno','to_bizno','rate'}] — from/to 는 복합키 node_id.
                   propagate_shock 이 그대로 소비 (필드명은 모델 시그니처 유지).
  init_sub_graph : {node_id: shock} — 시드만.
  nodes          : [AssembledNode] — node_id ↔ (bizno, upchecd, 기업명, is_seed).
  depth/rate_kind/within_subgraph/warnings : 진단.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import text

from nice_graph.shock.propagate import ShockResult, propagate_shock
from nice_graph.shock.screen import PrimarySelectionResult
from nice_poc.db import get_pg_engine

log = logging.getLogger(__name__)

NODE_SEP = "|"

# 전파 방향 — 셀러(from)→바이어(to) 가 company_edge 의 저장 방향.
# (라벨/가중치는 문서 기준: 매출 파급=매출처(고객) 방향, 매입 파급=매입처(공급사) 방향.)
#   downstream : 셀러→바이어 (저장 방향 그대로). 하류/매출 파급(매출처). 가중치 A.
#                rate 정규화 = 셀러(source)의 outgoing(총매출) 대비.
#   upstream   : 바이어→셀러 (방향 뒤집음). 상류/매입 파급(매입처). 가중치 B.
#                rate 정규화 = 바이어(source)의 incoming(총매입) 대비.
# ※ 정규화 분모는 normalize 옵션으로 방향과 분리해 고른다 (아래 Normalize 참고).
Direction = Literal["downstream", "upstream"]

# rate 정규화 기준 — 분모를 어느 끝 기준으로 잡는가 (방향과 직교).
#   source       : 전파 source(orientation 의 출발 노드) 의 outgoing 합. Σ_out≤W·α≤1
#                  → ρ(R)≤W·α<1 **절대수렴 보장**. (기본값)
#   counterparty : 거래상대(orientation 의 도착 노드) 기준 = 경제적 '매출/매입 비중' 라벨
#                  에 충실. 단 Σ_out 이 1 을 넘을 수 있어 **수렴 보장 약화**(damping 의존,
#                  발산 시 propagate_shock 이 converged=False 로 표면화). 경고 부여.
#
#   분모 컬럼 (PARTITION) 매핑:
#     direction \ normalize | source        | counterparty
#     downstream(셀러s→바이어t) | from(셀러 총매출) | to(바이어 총매입)
#     upstream  (바이어t→셀러s) | to(바이어 총매입) | from(셀러 총매출)
Normalize = Literal["source", "counterparty"]


# ── 복합키 헬퍼 ───────────────────────────────────────────────────────────────


def make_node_id(bizno: str, upchecd: str | None) -> str:
    """(bizno, upchecd) → 복합 node_id. upchecd 없으면 'bizno|' (식별 한계 표면화)."""
    return f"{bizno}{NODE_SEP}{upchecd or ''}"


def parse_node_id(node_id: str) -> tuple[str, str | None]:
    bizno, _, up = node_id.partition(NODE_SEP)
    return bizno, (up or None)


# ── 결과 타입 ─────────────────────────────────────────────────────────────────


@dataclass
class AssembledNode:
    node_id: str
    bizno: str
    upchecd: str | None
    korentrnm: str | None
    is_seed: bool
    seed_shock: float  # 시드면 초기 충격, 아니면 0.0


@dataclass
class PropagationInput:
    edges: list[dict] = field(default_factory=list)  # {'from_bizno','to_bizno','rate'}
    init_sub_graph: dict[str, float] = field(default_factory=dict)
    nodes: list[AssembledNode] = field(default_factory=list)
    depth: int = 0
    rate_kind: str = "all_rate"
    within_subgraph: bool = True
    damping: float = 0.85
    direction: str = "downstream"  # downstream(하류/매입,B) | upstream(상류/매출,A)
    direction_weight: float = 1.0  # 방향 비중 가중치 (A 또는 B). rate 에 곱.
    normalize: str = "source"  # source(수렴보장) | counterparty(매출·매입 비중 라벨)
    warnings: list[str] = field(default_factory=list)

    def node_index(self) -> dict[str, AssembledNode]:
        return {n.node_id: n for n in self.nodes}


# ── SQL ───────────────────────────────────────────────────────────────────────
#
# 핵심: depth 확장(순회) + 중복합산 + 정규화를 **재귀 CTE 한 쿼리**로 처리.
#   reach    : seeds 에서 무방향(from/to 양쪽) 으로 depth 까지 도달한 노드 집합.
#   induced  : 도달 노드 *사이의 모든 엣지* (유도 부분그래프) — (from,to) 별 sly_amt 합.
#              GROUP BY 로 중복행 자동 합산, 같은 depth 노드끼리의 '수평' 엣지도 포함
#              (홉-바이-홉 BFS 가 놓치던 엣지까지 정확히 잡힘).
#   out_total: source 의 *전체* outgoing 합 (leakage 분모용).
# 정규화 source = {src} (downstream→from_bizno, upstream→to_bizno). 방향을 뒤집으면
#   분모 PARTITION 도 새 source 기준으로 바뀌어야 Σ_out≤1(수렴) 이 유지된다.
# 반환행: from, to, amt, sub_total(서브그래프 내 source 합), full_total(전체 source 합).
_EXPAND_SQL_TMPL = """
    WITH RECURSIVE reach(bizno, depth) AS (
            SELECT x, 0 FROM unnest(CAST(:seeds AS text[])) AS x
        UNION
            SELECT CASE WHEN e.from_bizno = r.bizno THEN e.to_bizno ELSE e.from_bizno END,
                   r.depth + 1
            FROM reach r
            JOIN public.company_edge e
              ON (e.from_bizno = r.bizno OR e.to_bizno = r.bizno)
             {year_join}
            WHERE r.depth < :depth
    ),
    nodes AS (SELECT DISTINCT bizno FROM reach),
    induced AS (
        SELECT from_bizno, to_bizno, COALESCE(SUM(sly_amt), 0)::float AS amt
        FROM public.company_edge
        WHERE from_bizno IN (SELECT bizno FROM nodes)
          AND to_bizno   IN (SELECT bizno FROM nodes)
          {year_where}
        GROUP BY from_bizno, to_bizno
    ),
    out_total AS (
        SELECT {src} AS src_bizno, COALESCE(SUM(sly_amt), 0)::float AS tot
        FROM public.company_edge
        WHERE {src} IN (SELECT bizno FROM nodes)
          {year_where}
        GROUP BY {src}
    )
    SELECT i.from_bizno,
           i.to_bizno,
           i.amt,
           SUM(i.amt) OVER (PARTITION BY i.{src}) AS sub_total,
           ot.tot                                  AS full_total
    FROM induced i
    JOIN out_total ot ON ot.src_bizno = i.{src}
    WHERE i.amt > 0
"""

_NODE_ATTR_SQL = text(
    """
    SELECT bizno, upchecd, korentrnm
    FROM public.company
    WHERE bizno = ANY(:biznos)
    """
)


# ── 확장 (재귀 CTE, depth N) ────────────────────────────────────────────────────


def _fetch_induced_edges(
    seeds: list[str],
    depth: int,
    trade_year: str | None,
    src_col: str = "from_bizno",
):
    """seeds → depth 도달 노드의 유도 부분그래프 엣지 (정규화 분모 포함).

    src_col: rate 정규화 source 컬럼 — downstream='from_bizno', upstream='to_bizno'.
             sub_total/full_total 은 이 컬럼 기준으로 집계된다.

    Returns: [(from_bizno, to_bizno, amt, sub_total, full_total)].
    """
    yr_clause = "AND CAST(e.trade_year AS text) = :yr" if trade_year is not None else ""
    yr_where = "AND CAST(trade_year AS text) = :yr" if trade_year is not None else ""
    sql = text(
        _EXPAND_SQL_TMPL.format(year_join=yr_clause, year_where=yr_where, src=src_col)
    )
    params: dict[str, object] = {"seeds": list(seeds), "depth": int(depth)}
    if trade_year is not None:
        params["yr"] = str(trade_year)
    with get_pg_engine().connect() as c:
        return c.execute(sql, params).fetchall()


def _fetch_node_attrs(biznos: list[str]) -> dict[str, tuple[str | None, str | None]]:
    """bizno → (upchecd, korentrnm). company 1:1 가정 (collision 은 호출부에서 경고)."""
    if not biznos:
        return {}
    out: dict[str, tuple[str | None, str | None]] = {}
    collisions: set[str] = set()
    with get_pg_engine().connect() as c:
        rows = c.execute(_NODE_ATTR_SQL, {"biznos": list(biznos)}).mappings().fetchall()
    for r in rows:
        b = r["bizno"]
        if b in out and out[b][0] != r["upchecd"]:
            collisions.add(b)
        out[b] = (r["upchecd"], r["korentrnm"])
    if collisions:
        log.warning("bizno→upchecd 1:N collision %d 건 (복합키 모호): %s", len(collisions), sorted(collisions)[:5])
    return out


# ── 시드 정규화 ────────────────────────────────────────────────────────────────


def _coerce_seeds(
    seeds: PrimarySelectionResult | Iterable[tuple[str, str | None]],
    seed_shock: float | str | Mapping[str, float],
) -> tuple[list[tuple[str, str | None]], dict[str, float]]:
    """입력 시드를 (bizno,upchecd) 리스트 + bizno→shock 로 정규화.

    seed_shock:
      float          — 모든 시드 균등.
      'score'        — PrimarySelectionResult.score 사용 (pairs 입력 시엔 1.0 폴백).
      Mapping        — {bizno: shock} per-seed (stateless API 용). 누락 bizno 는 1.0.
    """
    is_map = isinstance(seed_shock, Mapping)

    def _shock_for(bizno: str, score: float | None = None) -> float:
        if is_map:
            return float(seed_shock.get(bizno, 1.0))  # type: ignore[union-attr]
        if seed_shock == "score":
            return float(score) if score is not None else 1.0
        return float(seed_shock)  # type: ignore[arg-type]

    pairs: list[tuple[str, str | None]] = []
    shock_by_bizno: dict[str, float] = {}
    if isinstance(seeds, PrimarySelectionResult):
        for f in seeds.firms:
            if not f.bizno:
                continue
            pairs.append((f.bizno, f.upchecd))
            shock_by_bizno[f.bizno] = _shock_for(f.bizno, f.score)
    else:
        for bizno, up in seeds:
            if not bizno:
                continue
            pairs.append((bizno, up))
            shock_by_bizno[bizno] = _shock_for(bizno)
    return pairs, shock_by_bizno


# ── public API ────────────────────────────────────────────────────────────────


def assemble_propagation_input(
    seeds: PrimarySelectionResult | Iterable[tuple[str, str | None]],
    *,
    depth: int = 3,
    trade_year: str | None = None,
    within_subgraph: bool = True,
    damping: float = 0.85,
    seed_shock: float | str | Mapping[str, float] = 1.0,
    direction: Direction = "downstream",
    direction_weight: float = 1.0,
    normalize: Normalize = "source",
    edge_overrides: Mapping[tuple[str, str], float] | None = None,
) -> PropagationInput:
    """1차 시드 → N-depth 확장 → propagate_shock 입력 조립.

    Args:
      seeds: PrimarySelectionResult(screen 결과) 또는 (bizno, upchecd) 쌍 iterable.
      depth: 확장 깊이 (과제 기본 3).
      trade_year: 거래연도 필터 (None=전 연도).
      within_subgraph: rate 분모를 서브그래프 안(True, Σ_out=1) / 전체 outgoing(False, leakage).
      damping: 홉당 충격 감쇠율 α (0<α<1). rate = α·(amt/denom) → Σ_out ≤ α < 1 →
               ρ(R) ≤ α < 1 로 **수렴 보장**. within_subgraph=True 는 정규화상
               Σ_out=1 (ρ=1) 이라 사이클에서 발산하므로 damping 으로 감쇠 필수.
               경제적 의미 = 각 거래단계에서 충격의 (1-α) 는 흡수, α 만 다음 단계로 전달.
      seed_shock: 초기 충격. float(균등) 또는 'score'(screen 점수 비례).
      direction: 전파 방향. ``downstream``(셀러→바이어, 하류/매출 파급=매출처, 가중치 A,
                 정규화=셀러 총매출) | ``upstream``(바이어→셀러, 상류/매입 파급=매입처,
                 가중치 B, 정규화=바이어 총매입). 방향에 따라 엣지 방향과 정규화
                 분모를 함께 뒤집어 Σ_out≤1(수렴) 을 유지한다. (라벨/가중치는 문서 기준)
      direction_weight: 방향 비중 가중치 A(상류)/B(하류). rate 에 곱하는 스칼라.
                        effective Σ_out ≤ direction_weight·damping → 이 곱이 ≤1 이어야
                        수렴 보장. >1 이면 warnings 로 표면화(발산 위험).
      normalize: rate 분모 기준 (방향과 직교). ``source``(전파 source 기준, Σ_out≤W·α≤1
                 **절대수렴 보장**, 기본) | ``counterparty``(거래상대 기준 = 경제적
                 매출/매입 비중 라벨 충실, 단 Σ_out>1 가능 → **수렴 보장 약화**,
                 발산 시 converged=False). 분모 컬럼 = source면 orientation 출발 노드,
                 counterparty면 도착 노드.
      edge_overrides: 거래 변화 시나리오용. {(from_bizno, to_bizno): g} — 저장 방향
                      (셀러→바이어) 기준 특정 엣지 비중에 0~1 인자 g 를 곱한다.
                      방향 무관하게 원 (from,to) 키로 매칭.

    Returns:
      PropagationInput — ``.edges`` / ``.init_sub_graph`` 를 propagate_shock 에 그대로 전달.
    """
    if not (0.0 < damping <= 1.0):
        raise ValueError(f"damping 은 (0,1] 범위여야 함: {damping}")
    if direction not in ("downstream", "upstream"):
        raise ValueError(f"direction 은 downstream|upstream: {direction!r}")
    if normalize not in ("source", "counterparty"):
        raise ValueError(f"normalize 는 source|counterparty: {normalize!r}")
    if direction_weight <= 0:
        raise ValueError(f"direction_weight 은 양수여야 함: {direction_weight}")
    warnings: list[str] = []
    if direction_weight * damping > 1.0:
        warnings.append(
            f"direction_weight({direction_weight})·damping({damping})={direction_weight * damping:.3f}>1 "
            "→ ρ(R) 가 1 을 넘어 발산할 수 있음 (수렴 보장 깨짐)"
        )
    if normalize == "counterparty":
        warnings.append(
            "normalize=counterparty: 거래상대(매출/매입 비중) 기준 정규화 → Σ_out≤1 보장이 "
            "아니라 수렴이 보장되지 않음(damping 의존). 발산 시 converged=False 로 표면화."
        )
    pairs, shock_by_bizno = _coerce_seeds(seeds, seed_shock)
    if not pairs:
        warnings.append("시드 없음 — screen 결과가 비었거나 bizno 매핑 실패")
        return PropagationInput(
            depth=depth,
            within_subgraph=within_subgraph,
            direction=direction,
            direction_weight=direction_weight,
            normalize=normalize,
            warnings=warnings,
        )

    seed_biznos = list(shock_by_bizno.keys())

    # 정규화 분모 컬럼 = (direction, normalize) 2×2.
    #   source       : orientation 출발(downstream→from, upstream→to)
    #   counterparty : orientation 도착(downstream→to, upstream→from)
    if normalize == "source":
        src_col = "from_bizno" if direction == "downstream" else "to_bizno"
    else:  # counterparty — 거래상대(매출/매입 라벨) 기준
        src_col = "to_bizno" if direction == "downstream" else "from_bizno"
    # 순회+중복합산+정규화분모 = 재귀 CTE 한 쿼리. rows: (from,to,amt,sub_total,full_total)
    rows = _fetch_induced_edges(seed_biznos, depth, trade_year, src_col=src_col)
    visited: set[str] = set(seed_biznos)
    for from_b, to_b, *_ in rows:
        visited.add(from_b)
        visited.add(to_b)

    # 노드 속성 (bizno → upchecd, 이름)
    attrs = _fetch_node_attrs(list(visited))
    missing_upchecd = [b for b in visited if attrs.get(b, (None, None))[0] is None]
    if missing_upchecd:
        warnings.append(f"upchecd 미상 노드 {len(missing_upchecd)}건 → 복합키 'bizno|' 로 표기")

    # 복합키 노드 id
    def nid(b: str) -> str:
        return make_node_id(b, attrs.get(b, (None, None))[0])

    # rate = direction_weight · damping · (amt/분모) · g.
    #   분모     = within_subgraph?(서브그래프내 source 합):(전체 outgoing 합).
    #   g        = 거래 변화 오버라이드 (저장방향 (from,to) 키, default 1.0).
    #   엣지방향 = downstream→nid(from)→nid(to), upstream→nid(to)→nid(from).
    overrides = edge_overrides or {}
    edges: list[dict] = []
    for from_b, to_b, amt, sub_total, full_total in rows:
        denom = float(sub_total if within_subgraph else (full_total or 0.0))
        if denom <= 0:
            continue
        g = float(overrides.get((from_b, to_b), 1.0))
        rate = direction_weight * damping * (float(amt) / denom) * g
        if rate <= 0:
            continue
        if direction == "downstream":
            src_id, dst_id = nid(from_b), nid(to_b)
        else:  # upstream — 바이어→셀러
            src_id, dst_id = nid(to_b), nid(from_b)
        edges.append({"from_bizno": src_id, "to_bizno": dst_id, "rate": rate})

    # init — 시드만 (복합키). bizno 단위로 한 번씩 (동일 bizno 가 복수 upchecd pair 로
    # 들어와도 nid 가 같아 이중합산되지 않도록 shock_by_bizno 키를 순회한다).
    init: dict[str, float] = {}
    for bizno, shock in shock_by_bizno.items():
        init[nid(bizno)] = init.get(nid(bizno), 0.0) + shock

    seed_id_set = set(init.keys())
    nodes: list[AssembledNode] = []
    for b in sorted(visited):
        up, name = attrs.get(b, (None, None))
        node_id = make_node_id(b, up)
        nodes.append(
            AssembledNode(
                node_id=node_id,
                bizno=b,
                upchecd=up,
                korentrnm=name,
                is_seed=node_id in seed_id_set,
                seed_shock=init.get(node_id, 0.0),
            )
        )

    rate_kind = "all_rate" if trade_year is None else f"years_rate:{trade_year}"
    log.info(
        "assemble: seeds=%d depth=%d dir=%s w=%.3f norm=%s → nodes=%d edges=%d (%s, within_subgraph=%s)",
        len(pairs), depth, direction, direction_weight, normalize, len(nodes), len(edges), rate_kind, within_subgraph,
    )
    return PropagationInput(
        edges=edges,
        init_sub_graph=init,
        nodes=nodes,
        depth=depth,
        rate_kind=rate_kind,
        within_subgraph=within_subgraph,
        damping=damping,
        direction=direction,
        direction_weight=direction_weight,
        normalize=normalize,
        warnings=warnings,
    )


def run_propagation(assembled: PropagationInput, **propagate_kwargs) -> ShockResult:
    """조립된 입력으로 propagate_shock 호출 (편의 래퍼). 결과의 bizno 는 복합키."""
    return propagate_shock(
        edges=assembled.edges,
        init_sub_graph=assembled.init_sub_graph,
        **propagate_kwargs,
    )
