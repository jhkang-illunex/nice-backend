"""D: RAGAS 생성지표 경량 judge 하니스 (폐쇄망·자체 LLM).

RAGAS 표준 라이브러리는 OpenAI judge 를 가정해 폐쇄망에 부적합하므로,
자체 LLM(qwen)을 judge 로 쓰는 경량 버전으로 생성(답변) 품질을 측정한다.
검색 랭킹(context precision/recall)은 hit@k·MRR(rag_final_report_eval)로 이미
측정하므로 여기서는 *답변* 지표 2종에 집중한다.

  faithfulness     : 답변의 HS부호가 citations(ground truth)에 근거하는가 = 환각률.
                     룰 기반 — 답변에서 10자리 부호를 추출해 citations 와 대조.
                     LLM 비용·변동 없이 결정적. (답변=거부/빈답이면 None=제외)
  answer_relevancy : 답변이 질의의 품목 의도에 적합한가. 자체 LLM judge(0~1).

한계: judge 가 답변 생성과 동일 모델(qwen2.5:7b)이라 self-judge 편향이 있다 —
PoC 시범 측정용. 본 사업에서는 상위 judge 모델·RAGAS 정식 통합으로 대체.

호출: python scripts/rag_eval_ragas.py            # 28문항 시범
      python scripts/rag_eval_ragas.py --full     # 120문항 + 시나리오
출력: /tmp/rag_ragas.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time

sys.path.insert(0, "scripts")
import httpx
from rag_final_report_eval import SCENARIO, SEEDS, _variants  # noqa: E402

from nice_llm import get_llm_json_client  # noqa: E402

BASE = "http://127.0.0.1:18002"
_HS = re.compile(r"(\d{10})")

_JUDGE_SYS = (
    "당신은 HS부호 검색 답변 평가기입니다. 질의와 답변을 보고 답변이 질의의 "
    "품목 의도에 얼마나 적합한지 0.0~1.0 으로 평가하세요. 품목이 정확히 매칭되면 "
    "1.0, 관련/부분매칭 0.5, 무관하거나 거부면 0.0. "
    '오직 JSON {"relevancy": <0.0~1.0 숫자>, "reason": "..."} 만 출력하세요.'
)


def agent(c: httpx.Client, q: str, k: int = 5) -> dict:
    r = c.get(f"{BASE}/api/hsk/agent", params={"q": q, "k": k})
    r.raise_for_status()
    return r.json()


def faithfulness(answer: str, citations: list[dict]) -> float | None:
    """답변 HS부호 ⊆ citations 비율. 답변에 부호가 없으면(거부) None."""
    cited = {h["hs_code"] for h in citations}
    ans_codes = _HS.findall(answer)
    if not ans_codes:
        return None
    grounded = sum(1 for code in ans_codes if code in cited)
    return grounded / len(ans_codes)


def relevancy(q: str, answer: str) -> float | None:
    try:
        res = get_llm_json_client().chat_json(
            messages=[
                {"role": "system", "content": _JUDGE_SYS},
                {"role": "user", "content": f"질의: {q}\n답변: {answer[:400]}"},
            ],
            temperature=0.0,
            max_tokens=128,
        )
        v = float(res.get("relevancy"))
        return max(0.0, min(1.0, v))
    except Exception:
        return None


def build_queries(full: bool) -> list[tuple[str, str]]:
    """(category, query) 목록. 기본은 시드별 keyword 1개 + 시나리오 supply/extra."""
    out: list[tuple[str, str]] = []
    if full:
        for idx, (name, _) in enumerate(SEEDS):
            for _, q in _variants(name, idx).items():
                out.append(("nl", q))
        for cat, q in SCENARIO:
            out.append((cat, q))
    else:
        # 시범: 시드 질의만 — 시나리오 supply/extra 는 agent CRAG 재검색으로
        # 문항당 수십 초 걸려 시범 규모에 부적합(별도 --full 에서 측정).
        for idx, (name, _) in enumerate(SEEDS):
            out.append(("nl", _variants(name, idx)["import_q"]))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--full", action="store_true")
    args = p.parse_args()

    queries = build_queries(args.full)
    rows = []
    with httpx.Client(timeout=120) as c:
        # 기동 확인
        for _ in range(45):
            try:
                c.get(f"{BASE}/api/hsk/search", params={"q": "립스틱", "limit": 1}).raise_for_status()
                break
            except Exception:
                time.sleep(2)
        t0 = time.perf_counter()
        for i, (cat, q) in enumerate(queries, 1):
            try:
                d = agent(c, q, 5)
            except Exception as e:  # noqa: BLE001
                rows.append({"cat": cat, "q": q, "faith": None, "rel": None, "err": str(e)})
                continue
            ans = d.get("answer", "")
            faith = faithfulness(ans, d.get("citations", []))
            rel = relevancy(q, ans)
            refused = faith is None
            rows.append({"cat": cat, "q": q, "faith": faith, "rel": rel, "refused": refused,
                         "answer": ans[:120]})
            if i % 5 == 0:
                print(f"  ... {i}/{len(queries)}", flush=True)
        wall = time.perf_counter() - t0

    faiths = [r["faith"] for r in rows if r["faith"] is not None]
    rels = [r["rel"] for r in rows if r["rel"] is not None]
    answered = sum(1 for r in rows if not r.get("refused"))
    out = {
        "n": len(rows),
        "answered": answered,
        "faithfulness_mean": statistics.mean(faiths) if faiths else None,
        "faithfulness_min": min(faiths) if faiths else None,
        "answer_relevancy_mean": statistics.mean(rels) if rels else None,
        "hallucination_rate": (1 - statistics.mean(faiths)) if faiths else None,
        "wall_s": round(wall, 1),
        "rows": rows,
    }
    with open("/tmp/rag_ragas.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print("\n" + "=" * 60)
    print(f"RAGAS 생성지표 (n={out['n']}, 답변={answered}, judge=qwen self)")
    print("=" * 60)
    if out["faithfulness_mean"] is not None:
        print(f"  faithfulness   mean={out['faithfulness_mean']:.3f}  min={out['faithfulness_min']:.3f}  "
              f"(환각률 {out['hallucination_rate']*100:.1f}%)")
    else:
        print("  faithfulness   N/A (답변 부호 없음)")
    rel = out["answer_relevancy_mean"]
    print(f"  answer_relevancy mean={rel:.3f}" if rel is not None else "  answer_relevancy N/A (judge 실패)")
    print(f"  wall={out['wall_s']}s")
    # 환각/저점 사례
    bad = [r for r in rows if r["faith"] is not None and r["faith"] < 1.0]
    if bad:
        print(f"\n  환각 사례 {len(bad)}건:")
        for r in bad[:5]:
            print(f"    faith={r['faith']:.2f} | {r['q'][:40]}")
    print("\n→ /tmp/rag_ragas.json 저장")


if __name__ == "__main__":
    main()
