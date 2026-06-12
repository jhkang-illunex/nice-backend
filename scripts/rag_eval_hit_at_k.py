"""HS RAG 자동 평가 — rag.hsk.name_ko 를 question, hs_code 를 정답으로 hit@k / MRR.

Stage A (사용자 결정 1단계)
  자동 평가셋: ``rag.hsk`` 의 (name_ko, hs_code) 페어를 그대로 사용.
  RAG 가 *자기 인덱스의 텍스트* 를 받아도 정답을 못 찾으면 *알고리즘 결함*.
  잘 맞추면 그 자체가 *상한선* (실 자연어 질의는 이보다 낮을 것).

호출 예
  python scripts/rag_eval_hit_at_k.py --n 500 --k 10 --workers 8
  python scripts/rag_eval_hit_at_k.py --n 0           # 전체 12,469 행 (~20분)
  python scripts/rag_eval_hit_at_k.py --relax-hs6     # HS6 prefix 까지 정답 인정
  python scripts/rag_eval_hit_at_k.py --jsonl out.jsonl  # per-query 결과 덤프

평가 흐름
  1. PG ``rag.hsk`` 에서 N 페어 SELECT (random sampling)
  2. 각 페어에 대해 GET /api/hsk/search?q=<name_ko>&limit=<k>
  3. 정답 hs_code 가 top-K 의 몇 번째에 있는지 추적
  4. hit@1 / hit@5 / hit@10 / MRR + latency 통계

매칭 모드
  strict (default) : hs_code 10자리 완전 일치
  --relax-hs6      : 정답과 hit 의 앞 6자리 (HS6) 일치만으로 인정
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

import httpx
from sqlalchemy import text

from nice_poc.db import get_pg_engine

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ─── 평가 셋 로딩 ────────────────────────────────────────────────────────────


def load_eval_set(n: int | None, *, unique_name: bool) -> list[tuple[str, str]]:
    """rag.hsk 에서 (name_ko, hs_code) 페어. n=None 이면 전체.

    unique_name=True 면 name_ko 가 *유일한 HS 코드와 매칭되는 row 만* — 동음이의어
    ('기타', '소의 것' 등) 제거.  그래야 데이터 모호성이 *상한선 측정* 을 흐트
    러뜨리지 않음.
    """
    if unique_name:
        base_sql = """
            WITH counts AS (
                SELECT name_ko, COUNT(*) AS n_codes
                FROM rag.hsk
                WHERE name_ko IS NOT NULL
                  AND length(btrim(name_ko)) >= 2
                GROUP BY name_ko
            )
            SELECT h.name_ko, h.hs_code
            FROM rag.hsk h
            JOIN counts c ON c.name_ko = h.name_ko
            WHERE c.n_codes = 1
        """
    else:
        base_sql = """
            SELECT name_ko, hs_code
            FROM rag.hsk
            WHERE name_ko IS NOT NULL
              AND length(btrim(name_ko)) >= 2
        """
    if n:
        sql = text(base_sql + " ORDER BY random() LIMIT :n")
        params = {"n": n}
    else:
        sql = text(base_sql)
        params = {}
    with get_pg_engine().connect() as c:
        rows = c.execute(sql, params).fetchall()
    return [(r[0], r[1]) for r in rows]


# ─── 호출 + 측정 ───────────────────────────────────────────────────────────


def _search(
    client: httpx.Client, base_url: str, q: str, k: int
) -> tuple[list[str], float]:
    """search 호출 → (hs_code 리스트, latency_seconds)."""
    t0 = time.perf_counter()
    r = client.get(
        f"{base_url.rstrip('/')}/api/hsk/search",
        params={"q": q, "limit": k},
    )
    r.raise_for_status()
    hs_codes = [h["hs_code"] for h in r.json()]
    return hs_codes, time.perf_counter() - t0


def _rank(target: str, hits: list[str], *, relax_hs6: bool) -> int | None:
    """1-based rank, None 이면 top-K 안에 없음."""
    if relax_hs6:
        t6 = target[:6]
        for i, h in enumerate(hits, 1):
            if h[:6] == t6:
                return i
        return None
    try:
        return hits.index(target) + 1
    except ValueError:
        return None


@dataclass
class QueryResult:
    query: str
    target: str
    hits: list[str]
    rank_strict: int | None
    rank_hs6: int | None
    latency_s: float


def evaluate(
    *,
    base_url: str,
    n: int | None,
    k: int,
    workers: int,
    timeout: float,
    jsonl_path: str | None,
    unique_name: bool,
) -> dict:
    queries = load_eval_set(n, unique_name=unique_name)
    log.info("Loaded %d queries from rag.hsk (unique_name=%s).", len(queries), unique_name)
    if not queries:
        log.error("No queries — is rag.hsk populated?")
        sys.exit(2)

    results: list[QueryResult] = []
    t_start = time.perf_counter()

    with httpx.Client(timeout=timeout) as client:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(_search, client, base_url, q, k): (q, target)
                for q, target in queries
            }
            done = 0
            for fut in as_completed(futs):
                q, target = futs[fut]
                try:
                    hits, latency = fut.result()
                except Exception as exc:
                    log.warning("err q=%r: %s", q[:30], exc)
                    continue
                results.append(
                    QueryResult(
                        query=q,
                        target=target,
                        hits=hits,
                        rank_strict=_rank(target, hits, relax_hs6=False),
                        rank_hs6=_rank(target, hits, relax_hs6=True),
                        latency_s=latency,
                    )
                )
                done += 1
                if done % 50 == 0:
                    log.info("  ... %d/%d", done, len(queries))

    wall = time.perf_counter() - t_start
    return _summarize(
        results, wall=wall, k=k, jsonl_path=jsonl_path, unique_name=unique_name
    )


def _summarize(
    results: list[QueryResult],
    *,
    wall: float,
    k: int,
    jsonl_path: str | None,
    unique_name: bool,
) -> dict:
    n = len(results)
    if n == 0:
        return {}

    def hits(ranks: list[int | None], p: int) -> float:
        return sum(1 for r in ranks if r is not None and r <= p) / n

    def mrr(ranks: list[int | None]) -> float:
        return sum(1.0 / r if r else 0.0 for r in ranks) / n

    strict_ranks = [r.rank_strict for r in results]
    hs6_ranks = [r.rank_hs6 for r in results]
    latencies = [r.latency_s for r in results]

    summary = {
        "n": n,
        "k": k,
        "unique_name": unique_name,
        "strict": {
            "found": sum(1 for r in strict_ranks if r is not None),
            "hit@1": hits(strict_ranks, 1),
            "hit@5": hits(strict_ranks, 5),
            "hit@10": hits(strict_ranks, min(10, k)),
            "mrr": mrr(strict_ranks),
        },
        "hs6_relax": {
            "found": sum(1 for r in hs6_ranks if r is not None),
            "hit@1": hits(hs6_ranks, 1),
            "hit@5": hits(hs6_ranks, 5),
            "hit@10": hits(hs6_ranks, min(10, k)),
            "mrr": mrr(hs6_ranks),
        },
        "latency_ms_p50": statistics.median(latencies) * 1000,
        "latency_ms_p95": _quantile(latencies, 0.95) * 1000,
        "wall_s": wall,
        "throughput_qps": n / wall if wall > 0 else 0.0,
    }

    log.info("")
    log.info("=" * 64)
    log.info(
        "RESULT  (n=%d, k=%d, unique_name=%s)", n, k, unique_name
    )
    log.info("=" * 64)
    log.info("                       strict           HS6-relax")
    log.info(
        "  found in top-K:      %5d (%5.1f%%)   %5d (%5.1f%%)",
        summary["strict"]["found"],
        summary["strict"]["found"] / n * 100,
        summary["hs6_relax"]["found"],
        summary["hs6_relax"]["found"] / n * 100,
    )
    log.info(
        "  hit@1:               %6.1f%%          %6.1f%%",
        summary["strict"]["hit@1"] * 100,
        summary["hs6_relax"]["hit@1"] * 100,
    )
    log.info(
        "  hit@5:               %6.1f%%          %6.1f%%",
        summary["strict"]["hit@5"] * 100,
        summary["hs6_relax"]["hit@5"] * 100,
    )
    log.info(
        "  hit@10:              %6.1f%%          %6.1f%%",
        summary["strict"]["hit@10"] * 100,
        summary["hs6_relax"]["hit@10"] * 100,
    )
    log.info(
        "  MRR:                 %7.4f         %7.4f",
        summary["strict"]["mrr"],
        summary["hs6_relax"]["mrr"],
    )
    log.info(
        "  latency p50/p95:     %.0f / %.0f ms      wall=%.1fs  (%.1f qps)",
        summary["latency_ms_p50"],
        summary["latency_ms_p95"],
        wall,
        summary["throughput_qps"],
    )

    if jsonl_path:
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        log.info("  per-query dump:  %s", jsonl_path)

    return summary


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(q * len(s)), len(s) - 1)
    return s[idx]


# ─── CLI ────────────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://localhost:18002")
    p.add_argument("--n", type=int, default=500, help="샘플 수 (0 = 전체)")
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument(
        "--unique-name",
        action="store_true",
        help="name_ko 가 *유일한* HS 코드 1개와 매칭되는 row 만 사용 (동음이의어 제거)",
    )
    p.add_argument("--jsonl", default=None, help="per-query 결과를 JSONL 로 덤프")
    args = p.parse_args()

    evaluate(
        base_url=args.base_url,
        n=None if args.n == 0 else args.n,
        k=args.k,
        workers=args.workers,
        timeout=args.timeout,
        jsonl_path=args.jsonl,
        unique_name=args.unique_name,
    )


if __name__ == "__main__":
    main()
