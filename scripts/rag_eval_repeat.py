"""C: 평가 재현성·noise floor 측정 — 동일 120문항 N회 반복 + 회차간 분산 집계.

목적
  최종 리포트(2026-06-18)는 120문항 *단발* 실행이라, v2(06-12) 대비
  hit@5·hit@10 각 -7.5%p 회귀가 *실제 퇴행* 인지 *측정 noise* 인지 구분 불가.
  문장형 질의는 서버 내부에서 LLM 품목추출(extract_goods)·CRAG 를 거치는데,
  CPU LLM 의 타임아웃·폴백은 temperature=0 이어도 회차마다 다른 결과를 낼 수
  있다 — 이 비결정성이 같은 문항의 rank 를 흔드는지 측정한다.

방법
  rag_final_report_eval 의 평가셋(SEEDS·_variants)·지표(metrics)를 그대로 재사용,
  동일 120문항을 --runs 회 반복. 각 회차의 hit@1/5/10·MRR 를 모으고
    1) 회차간 mean/std/min/max  (= noise floor)
    2) per-query rank 변동       (회차마다 rank 가 바뀌는 문항 = 비결정성 발원지)
  를 집계. v2 기준선과의 차이가 std 의 몇 배인지로 회귀 유의성 판정.

출력: /tmp/rag_eval_repeat.json (+ stdout 요약)
호출: python scripts/rag_eval_repeat.py --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rag_final_report_eval import SEEDS, metrics, rank_of, search, _variants  # noqa: E402

# v2 기준선 (RAG_RAGAS_EXTENDED 2026-06-12) — 회귀 유의성 판정 기준
V2_BASELINE = {"hit@1": 0.650, "hit@5": 0.950, "hit@10": 1.000, "mrr": 0.777}


def _jobs() -> list[tuple[str, str, str, list[str], str]]:
    out = []
    for idx, (name, prefixes) in enumerate(SEEDS):
        for tmpl, q in _variants(name, idx).items():
            out.append((f"{idx:02d}-{tmpl}", tmpl, name, prefixes, q))
    return out


def run_once(client: httpx.Client, jobs) -> list[dict]:
    """워커1 순차 — 단일 CPU LLM 경합으로 인한 인공적 retrieval 저하 방지."""
    rows = []
    for qid, tmpl, name, prefixes, q in jobs:
        try:
            hits, _ = search(client, q, 10)
            rk = rank_of(prefixes, hits)
            top1 = hits[0]["hs_code"] if hits else None
        except Exception as e:  # noqa: BLE001
            rk, top1 = None, f"(err: {e})"
        rows.append({"id": qid, "tmpl": tmpl, "seed": name, "rank": rk, "top1": top1})
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", type=int, default=3, help="반복 회차 수 (>=2 권장)")
    args = p.parse_args()

    jobs = _jobs()
    per_run: list[dict] = []
    per_q: dict[str, list[int | None]] = {}
    per_q_top1: dict[str, list[str | None]] = {}

    with httpx.Client(timeout=120.0) as client:
        for r in range(args.runs):
            t0 = time.perf_counter()
            rows = run_once(client, jobs)
            m = metrics([x["rank"] for x in rows])
            per_run.append(m)
            for x in rows:
                per_q.setdefault(x["id"], []).append(x["rank"])
                per_q_top1.setdefault(x["id"], []).append(x["top1"])
            print(
                f"[run {r + 1}/{args.runs}] "
                f"hit@1={m['hit@1'] * 100:5.1f}% hit@5={m['hit@5'] * 100:5.1f}% "
                f"hit@10={m['hit@10'] * 100:5.1f}% MRR={m['mrr']:.3f} found={m['found']} "
                f"({time.perf_counter() - t0:.0f}s)",
                flush=True,
            )

    # ── 회차간 집계 ──────────────────────────────────────────────────────────
    def agg(key: str) -> dict:
        vals = [m[key] for m in per_run]
        std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        return {
            "mean": statistics.mean(vals),
            "std": std,
            "min": min(vals),
            "max": max(vals),
            "spread": max(vals) - min(vals),
        }

    metric_agg = {k: agg(k) for k in ["hit@1", "hit@5", "hit@10", "mrr"]}

    # ── 회귀 유의성: v2 기준선과의 차이가 std 의 몇 배인가 ───────────────────
    regression = {}
    for k, base in V2_BASELINE.items():
        a = metric_agg[k]
        delta = a["mean"] - base
        sigma = delta / a["std"] if a["std"] > 0 else None
        regression[k] = {
            "v2": base,
            "mean": round(a["mean"], 4),
            "delta": round(delta, 4),
            "std": round(a["std"], 4),
            "sigma": (round(sigma, 2) if sigma is not None else None),
            "verdict": _verdict(delta, a["std"]),
        }

    # ── 비결정성 발원지: rank 가 회차마다 다른 문항 ─────────────────────────
    unstable = []
    for qid, ranks in per_q.items():
        if len(set(map(_rk_key, ranks))) > 1:
            unstable.append({
                "id": qid,
                "ranks": ranks,
                "top1s": per_q_top1[qid],
            })
    unstable.sort(key=lambda u: u["id"])

    out = {
        "runs": args.runs,
        "per_run": per_run,
        "metric_agg": metric_agg,
        "regression_vs_v2": regression,
        "unstable_queries": unstable,
        "n_unstable": len(unstable),
        "n_total": len(jobs),
    }
    with open("/tmp/rag_eval_repeat.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    # ── 요약 ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(f"재현성 집계 (runs={args.runs})")
    print("=" * 70)
    print(f"  {'지표':8} {'mean':>7} {'std':>7} {'min':>7} {'max':>7} {'spread':>7}")
    for k in ["hit@1", "hit@5", "hit@10", "mrr"]:
        a = metric_agg[k]
        sc = 100 if k != "mrr" else 1
        u = "%" if k != "mrr" else " "
        print(
            f"  {k:8} {a['mean'] * sc:6.1f}{u} {a['std'] * sc:6.2f}{u} "
            f"{a['min'] * sc:6.1f}{u} {a['max'] * sc:6.1f}{u} {a['spread'] * sc:6.2f}{u}"
        )
    print("\n  회귀 유의성 (v2 06-12 대비, |sigma|>=2 면 noise 아닌 실제 퇴행):")
    for k, r in regression.items():
        print(f"    {k:8} v2={r['v2']:.3f} → mean={r['mean']:.3f} "
              f"Δ={r['delta']:+.4f} ({r['sigma']}σ) {r['verdict']}")
    print(f"\n  비결정 문항: {len(unstable)}/{len(jobs)} (회차마다 rank 변동)")
    for u in unstable[:15]:
        print(f"    {u['id']:18} ranks={u['ranks']}")
    print("\n→ /tmp/rag_eval_repeat.json 저장")


def _rk_key(r: int | None) -> str:
    return "miss" if r is None else str(r)


def _verdict(delta: float, std: float) -> str:
    if std == 0:
        return "고정(변동없음)" if delta == 0 else ("실제차이" if abs(delta) > 0.001 else "동일")
    sigma = abs(delta) / std
    if sigma < 1:
        return "noise 범위내"
    if sigma < 2:
        return "경계(추가표본 필요)"
    return "실제 퇴행/개선"


if __name__ == "__main__":
    main()
