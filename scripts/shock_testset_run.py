"""외생충격 시나리오 테스트셋 러너 — 합성 12노드 그래프 4시나리오 → MD.

DB 없이 `docs/reports/shock/testset/graph.json` 의 합성 그래프를 **실제 엔진**
(run_tariff_shock / run_transaction_change)에 주입해 4개 시나리오를 돌린다.
주입은 assemble 의 DB 페치 두 함수(_fetch_induced_edges / _fetch_node_attrs)를
그래프 기반 in-memory 구현으로 monkeypatch 하는 방식 — 정규화·방향·감쇠·오버라이드
·difference-of-runs 등 모든 계산은 운영 코드 그대로 수행된다.

  python scripts/shock_testset_run.py
  → docs/reports/shock/testset/SHOCK_TESTSET_REPORT.md
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import nice_graph.shock.assemble as asm
from nice_graph.shock.assemble import assemble_propagation_input
from nice_graph.shock.scenario import run_scenario

HERE = Path(__file__).resolve().parent.parent
GRAPH_JSON = HERE / "docs/reports/shock/testset/graph.json"
OUT_MD = HERE / "docs/reports/shock/testset/SHOCK_TESTSET_REPORT.md"


def _install_graph(graph: dict) -> None:
    """그래프를 assemble 의 DB 페치 자리에 주입(monkeypatch)."""
    edges = [(e["from"], e["to"], float(e["amount"])) for e in graph["edges"]]
    name_by_bizno = {n["bizno"]: n["name"] for n in graph["nodes"]}
    up_by_bizno = {n["bizno"]: n["upchecd"] for n in graph["nodes"]}

    def fake_fetch_induced_edges(seed_biznos, depth, trade_year, src_col="from_bizno"):
        # 운영 SQL 의 induced + out_total 재현: 합성 그래프 전체가 곧 유도 부분그래프.
        # sub_total = SUM(amt) OVER (PARTITION BY src_col), full_total = 동일(전체=서브그래프).
        part: dict[str, float] = defaultdict(float)
        for f, t, amt in edges:
            part[f if src_col == "from_bizno" else t] += amt
        rows = []
        for f, t, amt in edges:
            tot = part[f if src_col == "from_bizno" else t]
            rows.append((f, t, amt, tot, tot))
        return rows

    def fake_fetch_node_attrs(biznos):
        return {b: (up_by_bizno.get(b), name_by_bizno.get(b)) for b in biznos}

    asm._fetch_induced_edges = fake_fetch_induced_edges  # noqa: SLF001
    asm._fetch_node_attrs = fake_fetch_node_attrs  # noqa: SLF001


def _run_one(graph: dict, sc: dict) -> dict:
    """시나리오 1건 실행 → 결과 dict (아규먼트 + 방향별 노드 결과)."""
    common = graph["common_args"]
    seeds = [(b, _up(graph, b)) for b in graph["seeds"]]
    args: dict = {
        "scenario": sc["scenario"],
        "seeds": seeds,
        "directions": sc["directions"],
        "weight_a": common["weight_a"],
        "weight_b": common["weight_b"],
        "depth": common["depth"],
        "damping": common["damping"],
        "normalize": common["normalize"],
        "within_subgraph": common["within_subgraph"],
        "seed_shock": common["seed_shock"],
    }
    if sc["scenario"] == "transaction_change":
        g = float(sc["factor"])
        args["edge_overrides"] = {(f, t): g for f, t in sc["override_edges"]}

    res = run_scenario(**args)
    name_by_id = {
        asm.make_node_id(n["bizno"], n["upchecd"]): n["name"] for n in graph["nodes"]
    }
    dirs = []
    for d in res.directions:
        rows = sorted(
            (
                {
                    "node": name_by_id.get(r["bizno"], r["bizno"]),
                    "value": r["shock"],
                }
                for r in d.result.shock_list
            ),
            key=lambda x: abs(x["value"]),
            reverse=True,
        )
        def _mtx(edges):
            return [
                {"src": name_by_id.get(e["from_bizno"], e["from_bizno"]),
                 "dst": name_by_id.get(e["to_bizno"], e["to_bizno"]),
                 "rate": e["rate"]}
                for e in edges
            ]

        # 거래 변화: 원본 W(오버라이드 미반영) 별도 조립 → baseline 매트릭스.
        matrix_base = None
        if sc["scenario"] == "transaction_change":
            base = _assemble_matrix(graph, d.direction, overrides=None)
            matrix_base = _mtx(base)
        dirs.append(
            {
                "direction": d.direction,
                "label": d.effect_label,
                "weight": d.weight,
                "n_nodes": len(d.assembled.nodes),
                "n_edges": len(d.assembled.edges),
                "total": d.result.total_shock,
                "iterations": d.result.iterations,
                "converged": d.result.converged,
                "rows": rows,
                "matrix": _mtx(d.assembled.edges),
                "matrix_base": matrix_base,
            }
        )
    return {"args": args, "warnings": list(res.warnings), "dirs": dirs}


def _up(graph: dict, bizno: str) -> str | None:
    for n in graph["nodes"]:
        if n["bizno"] == bizno:
            return n["upchecd"]
    return None


def _assemble_matrix(graph: dict, direction: str, overrides):
    """주어진 방향·오버라이드로 조립한 환산 엣지(edges) 반환 — 매트릭스용."""
    common = graph["common_args"]
    seeds = [(b, _up(graph, b)) for b in graph["seeds"]]
    w = common["weight_a"] if direction == "downstream" else common["weight_b"]
    pin = assemble_propagation_input(
        seeds,
        depth=common["depth"],
        direction=direction,
        direction_weight=w,
        damping=common["damping"],
        normalize=common["normalize"],
        within_subgraph=common["within_subgraph"],
        seed_shock=common["seed_shock"],
        edge_overrides=overrides,
    )
    return pin.edges


# ── MD 렌더 ──────────────────────────────────────────────────────────────────


def _fmt_args(args: dict, graph: dict) -> str:
    lines = [
        f"- `scenario` = `{args['scenario']}`",
        f"- `seeds` = {[n['name'] for n in graph['nodes'] if n['bizno'] in graph['seeds']]}"
        f"  (bizno: {graph['seeds']})",
        f"- `directions` = `{args['directions']}`",
        f"- `weight_a` (매출/하류·A) = `{args['weight_a']}` · `weight_b` (매입/상류·B) = `{args['weight_b']}`",
        f"- `depth` = `{args['depth']}` · `damping` = `{args['damping']}` · "
        f"`normalize` = `{args['normalize']}` · `within_subgraph` = `{args['within_subgraph']}`",
        f"- `seed_shock` = `{args['seed_shock']}` (시드 3곳 균등)",
    ]
    if "edge_overrides" in args:
        nm = {n["bizno"]: n["name"] for n in graph["nodes"]}
        ov = "; ".join(
            f"{nm.get(f, f)}→{nm.get(t, t)}=×{g}" for (f, t), g in args["edge_overrides"].items()
        )
        lines.append(f"- `edge_overrides` (저장방향 셀러→바이어, g) = {len(args['edge_overrides'])}건: {ov}")
    return "\n".join(lines)


def _render_mermaid(graph: dict) -> list[str]:
    """방향성 엣지(셀러→바이어) + 거래금액 라벨 그래프 다이어그램(Mermaid)."""
    nm = {n["bizno"]: n["name"] for n in graph["nodes"]}

    def nid(b: str) -> str:
        return "N" + b

    out = ["```mermaid", "flowchart LR"]
    out.append("  classDef seed fill:#ffe9a8,stroke:#d4a017,stroke-width:2px;")
    out.append("  classDef tier3 fill:#eef2f7,stroke:#9bb0c9;")
    for n in graph["nodes"]:
        out.append(f'  {nid(n["bizno"])}["{n["name"]}"]')
    for e in graph["edges"]:
        out.append(f'  {nid(e["from"])} -->|"{e["amount"]:,}"| {nid(e["to"])}')
    seed_ids = ",".join(nid(b) for b in graph["seeds"])
    out.append(f"  class {seed_ids} seed;")
    tier3 = [nid(n["bizno"]) for n in graph["nodes"] if n["tier"] == 3]
    if tier3:
        out.append(f"  class {','.join(tier3)} tier3;")
    out.append("```")
    out.append("")
    out.append(f"_노드 색: 노란=시드(1차), 회색=3차. 화살표=셀러→바이어, 라벨=거래금액(원). (총 {len(nm)}노드)_")
    out.append("")
    return out


def _short(name: str) -> str:
    """매트릭스 헤더용 짧은 라벨: '공급사P(2차)'→'공급P', '고객A(2차)'→'고객A'."""
    return name.split("(")[0].replace("공급사", "공급")


def _render_matrix(matrix: list[dict]) -> list[str]:
    """환산 엣지(rate)를 행=소스·열=타깃 매트릭스로. 참여 노드만(빈 행/열 제외)."""
    if not matrix:
        return ["_(엣지 없음)_", ""]
    cell: dict[tuple[str, str], float] = {}
    srcs: list[str] = []
    dsts: list[str] = []
    for e in matrix:
        s, d = _short(e["src"]), _short(e["dst"])
        cell[(s, d)] = e["rate"]
        if s not in srcs:
            srcs.append(s)
        if d not in dsts:
            dsts.append(d)
    srcs.sort()
    dsts.sort()
    out = ["행=전파 소스 · 열=전파 타깃 · 값=환산 rate (= weight·damping·amt/Σ_source·g)", ""]
    out.append("| src＼dst | " + " | ".join(dsts) + " |")
    out.append("|---|" + "---|" * len(dsts))
    for s in srcs:
        cells = [f"{cell[(s, d)]:.3f}" if (s, d) in cell else "" for d in dsts]
        out.append(f"| **{s}** | " + " | ".join(cells) + " |")
    out.append("")
    return out


def build_md(graph: dict, results: list[dict]) -> str:
    md: list[str] = []
    m = graph["meta"]
    md.append(f"# {m['title']}\n")
    md.append(f"> {m['description']}\n")
    md.append(
        "> 엔진: 실제 `run_tariff_shock` / `run_transaction_change` (DB 대신 합성 그래프 주입). "
        "모든 계산(정규화·방향·감쇠·오버라이드·Δ)은 운영 코드 그대로.\n"
    )

    # 그래프 구조
    md.append("## 1. 테스트 그래프 구조 (12노드)\n")
    md.append(f"- 시드(1차) {len(graph['seeds'])}곳 + depth-3 연결 노드 = 총 {len(graph['nodes'])}노드, "
              f"{len(graph['edges'])}엣지. `edge_direction`: {m['edge_direction']}\n")
    md.append("### 그래프 다이어그램 (방향성 엣지 + 거래금액)\n")
    md.extend(_render_mermaid(graph))
    md.append("### 노드\n")
    md.append("| bizno | 기업명 | tier | 시드 |")
    md.append("|---|---|---|---|")
    for n in graph["nodes"]:
        md.append(f"| {n['bizno']} | {n['name']} | {n['tier']}차 | {'✅' if n['is_seed'] else ''} |")
    md.append("\n### 엣지 (셀러→바이어, 금액)\n")
    md.append("| 셀러(from) | 바이어(to) | 금액(원) | 설명 |")
    md.append("|---|---|---|---|")
    nm = {n["bizno"]: n["name"] for n in graph["nodes"]}
    for e in graph["edges"]:
        md.append(f"| {nm[e['from']]} | {nm[e['to']]} | {e['amount']:,} | {e['note']} |")
    md.append("")

    # 시나리오별
    md.append("## 2. 시나리오별 결과\n")
    md.append("각 시나리오의 **사용 아규먼트 전체**와 방향별 전파 결과(노드 충격/변화분 Δ).\n")
    for sc, r in zip(graph["scenarios"], results, strict=True):
        md.append(f"### 2.{graph['scenarios'].index(sc) + 1} {sc['title']}\n")
        md.append("**사용 아규먼트**\n")
        md.append(_fmt_args(r["args"], graph) + "\n")
        for d in r["dirs"]:
            kind = "변화분 Δ" if sc["scenario"] == "transaction_change" else "누적 충격"
            md.append(f"**방향: {d['direction']} ({d['label']})** — "
                      f"노드 {d['n_nodes']} · 엣지 {d['n_edges']} · "
                      f"수렴 {'✅' if d['converged'] else '❌'}({d['iterations']}회) · "
                      f"Σ{kind}={d['total']:.6f}\n")
            if sc["scenario"] == "transaction_change":
                md.append("_① 원본 weight 매트릭스 W — 매입/매출 감소 미반영 (g=1)_\n")
                md.extend(_render_matrix(d["matrix_base"]))
                md.append("_② 가중치 반영 매트릭스 W′ — 시드↔2차 오버라이드 ×g 적용 (전파에 실제 투입)_\n")
                md.extend(_render_matrix(d["matrix"]))
            else:
                md.append("_환산 엣지 매트릭스 R — 전파 계산에 실제 투입된 값_\n")
                md.extend(_render_matrix(d["matrix"]))
            md.append("_전파 결과_\n")
            md.append(f"| 노드 | {kind} |")
            md.append("|---|---|")
            for row in d["rows"]:
                if abs(row["value"]) < 1e-12:
                    continue
                md.append(f"| {row['node']} | {row['value']:+.6f} |")
            md.append("")
        if r["warnings"]:
            for w in r["warnings"]:
                md.append(f"> ⚠ {w}")
            md.append("")

    # 재현 안내
    md.append("## 3. 재현 / 편집\n")
    md.append(
        "- 그래프·시드·시나리오 정의: `docs/reports/shock/testset/graph.json` "
        "(노드/엣지/금액/오버라이드 대상 모두 편집 가능)\n"
        "- 재실행: `python scripts/shock_testset_run.py` → 이 리포트 갱신\n"
        "- 거래 변화 변화분 Δ = (수정W 전파) − (원W 전파) [difference-of-runs]. "
        "g<1(감소)이면 보통 Δ<0.\n"
    )
    return "\n".join(md)


def main() -> None:
    graph = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    _install_graph(graph)
    results = [_run_one(graph, sc) for sc in graph["scenarios"]]
    OUT_MD.write_text(build_md(graph, results), encoding="utf-8")
    print(f"MD 작성: {OUT_MD}")
    for sc, r in zip(graph["scenarios"], results, strict=True):
        for d in r["dirs"]:
            print(f"  {sc['key']:16} {d['direction']:10} Σ={d['total']:+.4f} "
                  f"conv={d['converged']} iter={d['iterations']}")


if __name__ == "__main__":
    main()
