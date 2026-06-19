"""최종 보고서용 RAG 재평가 — 자연어 120문항(RAGAS hit@k/MRR) + 시나리오 13문항.

라이브 rag-server(/api/hsk/search, /api/hsk/agent)에 대해 실제 실행한다.

평가셋
  A. 자연어 120문항 = 20 시드 × 6 템플릿(keyword/import_q/tariff_q/
     country_declare/hs_wonder/export_sebun). 기대 prefix 와 rank 매칭으로
     hit@1/5/10 · MRR (RAGAS 방법론의 검색 랭킹 지표 부분).
  B. 시나리오 13문항 = 공급망 6 + 비관련 5 + 추가검증 2. /search top3 +
     /agent 답변·거부 + 판정.

출력: /tmp/final_eval.json
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

BASE = "http://127.0.0.1:18002"

# ── A. 자연어 120문항 시드 (v2 부록의 기대 prefix) ──────────────────────────
SEEDS: list[tuple[str, list[str]]] = [
    ("전기차", ["870380", "870240", "8507"]),
    ("순수 전기 승용차 BEV", ["870380"]),
    ("열연강판", ["7208"]),
    ("스테인리스 냉연강판", ["7219", "7220"]),
    ("립스틱", ["330410"]),
    ("기초화장품 스킨 로션", ["330499"]),
    ("두바이유 원유", ["2709"]),
    ("나프타", ["271012"]),
    ("정제 구리 전기동", ["7403"]),
    ("반도체 리드프레임용 구리합금 판", ["7409", "7410"]),
    ("LNG 액화천연가스", ["271111"]),
    ("프로판 가스", ["271112"]),
    ("네오디뮴 영구자석", ["850511"]),
    ("희토류 금속 세륨", ["280530", "2846"]),
    ("반도체용 감광액 포토레지스트", ["370790"]),
    ("에칭용 불화수소산", ["281111"]),
    ("제빵용 밀가루", ["1101"]),
    ("듀럼밀", ["100111", "100119", "1001"]),
    ("자동차 브레이크 패드", ["870830"]),
    ("전기차 배터리 리튬이온 배터리셀", ["850760"]),
]

_COUNTRIES = ["미국", "중국", "일본", "독일", "프랑스", "베트남", "호주", "인도"]


def _eul(name: str) -> str:
    """목적격 조사 — 마지막 글자 받침 유무로 을/를 결정 (영문 약어는 를)."""
    last = name.strip()[-1]
    if "가" <= last <= "힣":
        has_jong = (ord(last) - 0xAC00) % 28 != 0
        return "을" if has_jong else "를"
    return "를"


def _variants(name: str, idx: int) -> dict[str, str]:
    c1 = _COUNTRIES[idx % len(_COUNTRIES)]
    c2 = _COUNTRIES[(idx + 3) % len(_COUNTRIES)]
    j = _eul(name)
    return {
        "keyword": name,
        "import_q": f"{name}{j} 수입할 때 어떤 HS 코드를 사용하나요?",
        "tariff_q": f"{name} 관세율이 얼마인가요",
        "country_declare": f"{c1}에서 {name}{j} 수입하려고 하는데 어떤 부호로 신고해야 하나요",
        "hs_wonder": f"{name}의 HS 부호가 궁금합니다",
        "export_sebun": f"{c2}로 {name}{j} 수출할 때 적용되는 세번 좀 알려주세요",
    }


# ── B. 시나리오 13문항 ───────────────────────────────────────────────────────
SCENARIO: list[tuple[str, str]] = [
    ("supply", "유럽연합(EU)이 한국산 철강 제품에 추가 관세를 부과했을 때 영향을 분석해줘."),
    ("supply", "철강 제품 관련 기업 알려줘."),
    ("supply", "철강 제품 수입이 중단되었을 때 영향을 받는 기업은?"),
    ("supply", "철강 제품 수입이 늘어나면 발생하는 파급효과는?"),
    ("supply", "WTI 가격이 20% 상승하면 어떤 기업이 영향을 받아?"),
    ("supply", "중국의 희토류 수출 제한 시 영향을 받는 기업은?"),
    ("unrelated", "김치볶음밥 레시피 알려줘."),
    ("unrelated", "오늘 서울 날씨 어때?"),
    ("unrelated", "아이폰과 갤럭시 중 뭐가 좋아?"),
    ("unrelated", "강아지 산책은 하루 몇 번 해야 해?"),
    ("unrelated", "엑셀 피벗테이블 만드는 방법 알려줘."),
    ("extra", "브릭스 값이 10 이하인 냉동 오렌지 주스는 어떤 품목 코드인가요"),
    ("extra", "중동 원유 가격이 오르고 있어요"),
]


# ── 호출 ─────────────────────────────────────────────────────────────────────


def search(client: httpx.Client, q: str, limit: int = 10):
    t0 = time.perf_counter()
    r = client.get(f"{BASE}/api/hsk/search", params={"q": q, "limit": limit})
    r.raise_for_status()
    return r.json(), time.perf_counter() - t0


def agent(client: httpx.Client, q: str, k: int = 5):
    r = client.get(f"{BASE}/api/hsk/agent", params={"q": q, "k": k})
    r.raise_for_status()
    return r.json()


def rank_of(prefixes: list[str], hits: list[dict]) -> int | None:
    for i, h in enumerate(hits, 1):
        code = str(h.get("hs_code", ""))
        if any(code.startswith(p) for p in prefixes):
            return i
    return None


# ── 실행 ─────────────────────────────────────────────────────────────────────


def run_120(client: httpx.Client) -> dict:
    jobs = []
    for idx, (name, prefixes) in enumerate(SEEDS):
        for tmpl, q in _variants(name, idx).items():
            jobs.append((f"{idx:02d}-{tmpl}", tmpl, name, prefixes, q))

    rows = []
    # 순차(워커1) — 단일 CPU LLM(추출·CRAG) 경합으로 인한 타임아웃·폴백 방지.
    # 병렬 실행 시 문장형 질의 retrieval 이 인공적으로 저하됨(측정 결함).
    with ThreadPoolExecutor(max_workers=1) as ex:
        futs = {ex.submit(search, client, j[4], 10): j for j in jobs}
        for fut in as_completed(futs):
            qid, tmpl, name, prefixes, q = futs[fut]
            try:
                hits, lat = fut.result()
            except Exception as e:
                rows.append({"id": qid, "tmpl": tmpl, "seed": name, "rank": None, "lat": None, "err": str(e)})
                continue
            rk = rank_of(prefixes, hits)
            rows.append({
                "id": qid, "tmpl": tmpl, "seed": name, "prefixes": prefixes,
                "rank": rk, "lat": lat,
                "top1": (hits[0]["hs_code"] if hits else None),
                "top1_name": (hits[0].get("name_ko") if hits else None),
            })
    return {"rows": rows}


def metrics(ranks: list[int | None]) -> dict:
    n = len(ranks)
    def hit(p): return sum(1 for r in ranks if r is not None and r <= p) / n
    mrr = sum(1.0 / r if r else 0.0 for r in ranks) / n
    return {"n": n, "hit@1": hit(1), "hit@5": hit(5), "hit@10": hit(10), "mrr": mrr,
            "found": sum(1 for r in ranks if r is not None)}


def run_scenario(client: httpx.Client) -> list[dict]:
    out = []
    for cat, q in SCENARIO:
        try:
            hits, _ = search(client, q, 3)
        except Exception:
            hits = []
        top3 = [{"hs_code": h["hs_code"], "name_ko": h.get("name_ko"), "score": round(h.get("score", 0.0), 4)} for h in hits[:3]]
        try:
            ag = agent(client, q, 5)
            ans = ag.get("answer", "")
        except Exception as e:
            ans = f"(agent 오류: {e})"
        refused = ("확실하지 않" in ans) or ("무관" in ans)
        search_empty = len(top3) == 0
        if cat == "unrelated":
            verdict = "거부(정상)" if (search_empty and refused) else ("부분거부" if refused or search_empty else "오추천")
        else:
            verdict = "유효 추천" if (top3 and not refused) else ("거부" if refused else "후보없음")
        out.append({"cat": cat, "q": q, "top3": top3, "answer": ans[:400], "refused": refused, "verdict": verdict})
    return out


def main() -> None:
    with httpx.Client(timeout=120.0) as client:
        t0 = time.perf_counter()
        res120 = run_120(client)
        wall120 = time.perf_counter() - t0

        rows = res120["rows"]
        overall = metrics([r["rank"] for r in rows])
        by_tmpl = {}
        for tmpl in ["keyword", "import_q", "tariff_q", "country_declare", "hs_wonder", "export_sebun"]:
            by_tmpl[tmpl] = metrics([r["rank"] for r in rows if r["tmpl"] == tmpl])
        # 시드별 MRR
        by_seed = {}
        for name, _ in SEEDS:
            sr = [r["rank"] for r in rows if r["seed"] == name]
            by_seed[name] = round(metrics(sr)["mrr"], 3)

        lats = [r["lat"] for r in rows if r.get("lat")]
        lats.sort()
        p50 = lats[len(lats) // 2] if lats else 0

        print("=== 120문항 ===")
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in overall.items()})
        for t, m in by_tmpl.items():
            print(f"  {t:16} hit@1={m['hit@1']*100:5.1f}% hit@5={m['hit@5']*100:5.1f}% hit@10={m['hit@10']*100:5.1f}% MRR={m['mrr']:.3f}")

        if "--reuse-scenario" in sys.argv:
            with open("/tmp/final_eval.json", encoding="utf-8") as f:
                scen = json.load(f)["scenario"]
            print(f"=== 시나리오 {len(scen)}문항 (이전 결과 재사용) ===")
        else:
            scen = run_scenario(client)
            print(f"=== 시나리오 {len(scen)}문항 ===")
            for s in scen:
                print(f"  [{s['cat']:9}] {s['verdict']:10} | {s['q'][:30]}")

    out = {
        "overall": overall, "by_tmpl": by_tmpl, "by_seed": by_seed,
        "rows": rows, "scenario": scen,
        "latency_p50_s": p50, "wall120_s": wall120,
        "n_seeds": len(SEEDS),
    }
    with open("/tmp/final_eval.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n→ /tmp/final_eval.json 저장")


if __name__ == "__main__":
    main()
