"""일반화 테스트 — 정형 6템플릿을 탈피한 다양한 실사용 스타일 질의로 hit@k.

기존 rag_final_report_eval 의 6템플릿은 *같은 문장 골격에 시드 품목명만 교체* 라
템플릿 과적합·동의어 사전 커버리지를 가려낼 수 없다. 이 셋은 의도적으로:
  - 구어/축약   : '립스틱 hs코드', '브레이크 패드 세번 알려줘'
  - 통칭/별칭   : '불산'(불화수소산), '스텐'(스테인리스), '엘엔지'(LNG), '휘발유 원료'(나프타)
  - 설명/맥락형 : '전기로만 달리는 자동차', '빵 만드는 밀가루', '파스타용 듀럼밀'
  - 한글표기/혼용: '엘엔지 천연가스', '에이치에스 코드'
를 섞어, 사전에 없는 새 표현에서도 일반화되는지 + 미등록 통칭 갭을 드러낸다.

각 항목: (정답 prefixes, 질의, 스타일). 정답은 기존 20시드와 동일 매핑.
출력: /tmp/rag_generalize.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time

sys.path.insert(0, "scripts")
import httpx
from rag_final_report_eval import rank_of  # noqa: E402

BASE = "http://127.0.0.1:18002"

# (정답 prefixes, 질의, 스타일) — 동의어 사전 미등록 통칭에 ★ 표시
GENERAL: list[tuple[list[str], str, str]] = [
    (["870380", "870240", "8507"], "전기로만 달리는 자동차는 어떤 코드로 신고하나요?", "설명형"),
    (["870380"], "배터리로만 굴러가는 순수 전기 승용차 세번이 궁금해요", "통칭/설명"),
    (["7208"], "열연 철판 수입하려는데 hs코드가 뭐죠", "구어/통칭★"),
    (["7219", "7220"], "스텐 냉연강판 분류코드 알려줘", "축약/별칭★(스텐)"),
    (["330410"], "립스틱 에이치에스 코드", "구어/한글표기"),
    (["330499"], "스킨이랑 로션 같은 기초화장품 수출할 때 세번", "맥락형"),
    (["2709"], "두바이산 원유 들여올 때 무슨 부호 쓰나요", "구어"),
    (["271012"], "휘발유 만드는 원료인 나프타 코드", "설명/통칭★"),
    (["7403"], "전해 정련한 전기동 구리 hs", "별칭"),
    (["7409", "7410"], "반도체 리드프레임에 쓰는 구리합금 박판 코드", "전문/맥락"),
    (["271111"], "엘엔지 천연가스 수입 부호 알려주세요", "한글표기★(엘엔지)"),
    (["271112"], "프로판가스 통관 코드", "구어"),
    (["850511"], "네오디뮴 자석 hs코드", "축약★(자석)"),
    (["280530", "2846"], "희토류 세륨 금속은 어디로 분류되나요", "전문"),
    (["370790"], "반도체 공정에 쓰는 감광액 포토레지스트 세번", "별칭/맥락"),
    (["281111"], "에칭에 쓰는 불산 hs코드", "통칭/별칭★(불산)"),
    (["1101"], "빵 만들 때 쓰는 밀가루 수입 코드", "설명형"),
    (["100111", "100119", "1001"], "파스타용 듀럼밀은 어떻게 분류하나요", "맥락형"),
    (["870830"], "자동차 브레이크 패드 세번 좀 알려줘", "구어"),
    (["850760"], "리튬이온 배터리셀 hs코드", "축약"),
]


def main() -> None:
    c = httpx.Client(timeout=120)
    for _ in range(45):
        try:
            c.get(f"{BASE}/api/hsk/search", params={"q": "립스틱", "limit": 1}).raise_for_status()
            break
        except Exception:
            time.sleep(2)

    def search(q: str, limit: int = 10):
        r = c.get(f"{BASE}/api/hsk/search", params={"q": q, "limit": limit})
        r.raise_for_status()
        return r.json()

    rows = []
    for pfx, q, style in GENERAL:
        try:
            hits = search(q, 10)
            rk = rank_of(pfx, hits)
            top1 = hits[0]["hs_code"] if hits else None
            top1_name = hits[0].get("name_ko") if hits else None
        except Exception as e:  # noqa: BLE001
            rk, top1, top1_name = None, f"(err {e})", None
        rows.append({"pfx": pfx, "q": q, "style": style, "rank": rk, "top1": top1, "top1_name": top1_name})

    ranks = [r["rank"] for r in rows]
    n = len(ranks)
    def hit(p): return sum(1 for r in ranks if r and r <= p) / n
    mrr = sum(1.0 / r if r else 0.0 for r in ranks) / n
    summary = {
        "n": n, "hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "hit@10": hit(10),
        "mrr": mrr, "found": sum(1 for r in ranks if r),
    }
    with open("/tmp/rag_generalize.json", "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)

    print("=" * 64)
    print(f"일반화 테스트 (n={n}, 정형6템플릿 탈피)")
    print("=" * 64)
    print(f"  hit@1={summary['hit@1']*100:.1f}%  hit@3={summary['hit@3']*100:.1f}%  "
          f"hit@5={summary['hit@5']*100:.1f}%  hit@10={summary['hit@10']*100:.1f}%  "
          f"MRR={summary['mrr']:.3f}  found={summary['found']}/{n}")
    print("\n  질의별 (rank — 스타일):")
    for r in rows:
        flag = "" if r["rank"] and r["rank"] <= 5 else "  ← 약점"
        print(f"    rank={str(r['rank']):4} {r['style']:18} | {r['q'][:34]}{flag}")
        if not r["rank"] or r["rank"] > 5:
            print(f"             top1={r['top1']} {r['top1_name']}")
    print("\n→ /tmp/rag_generalize.json 저장")


if __name__ == "__main__":
    main()
