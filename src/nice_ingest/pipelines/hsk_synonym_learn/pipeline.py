"""동의어 self-play 학습 — 저신뢰 질의를 LLM 후보 + 검색 점수 검증으로 보완.

폐쇄망 자기보완 루프의 배치 단계 (야간 cron 등으로 주기 실행):

  1. ``rag.search_log`` 의 미해결 저신뢰 질의(distinct)를 수집
  2. 로컬 LLM(qwen)에게 관세율표 공식 용어 후보 N개 생성 요청
  3. **self-play 검증**: 각 후보를 질의에 덧붙여 실제 hybrid 검색 실행 —
     top1 점수가 ``RAG_SYN_VERIFY_THRESHOLD`` 이상이고 원질의 대비 개선됐을
     때만 ``rag.synonyms`` 에 등록 (LLM 환각 후보는 여기서 탈락)
  4. 처리한 질의는 resolved 마킹 (재처리 방지)

LLM 을 '판단자'가 아니라 '후보 생성기'로만 쓰는 것이 설계 핵심 — 등록 여부는
LLM 이 아니라 검색 점수가 결정하므로, 작은 모델이어도 루프가 오염되지 않는다.
"""

from __future__ import annotations

import argparse
import logging
import sys

log = logging.getLogger(__name__)

# few-shot 예시는 검증된 빌트인 사전(nice_rag.search.synonyms._BUILTIN)에서 발췌
_CANDIDATE_PROMPT = (
    "당신은 한국 관세청 관세율표(HSK) 전문가입니다. "
    "검색어와 같은 품목을 가리키는, 관세율표 품목분류에서 실제로 쓰이는 "
    "한국어 공식 용어 후보를 5개 제시하세요.\n\n"
    "예시:\n"
    "검색어: 희토류 → 희토류금속\n"
    "검색어: 전기동 → 정제한 구리 음극\n"
    "검색어: 리튬이온 배터리 → 리튬이온 축전지\n"
    "검색어: 화장품 → 미용이나 메이크업용 제품류\n\n"
    "규칙: 한 줄에 하나씩. 번호·설명·영어·한자·HS코드 없이 한국어 용어만 출력.\n\n"
    "검색어: {query}"
)

def _gen_candidates(query: str) -> list[str]:
    import re

    from nice_rag.clients import get_llm_client

    text = get_llm_client().chat(
        messages=[{"role": "user", "content": _CANDIDATE_PROMPT.format(query=query)}],
        temperature=0.5,
        max_tokens=200,
    )
    cands = []
    for line in text.splitlines():
        c = line.strip().lstrip("0123456789.-·* ")
        c = c.split("→")[-1].strip()  # ' 검색어: x → y' 형식 출력 방어
        if 0 < len(c) <= 40 and re.fullmatch(r"[가-힣0-9 ()]+", c):
            cands.append(c)
    return cands[:5]


def _topk(query_text: str, k: int = 3) -> list[tuple[str, float]]:
    from nice_rag.search.hsk_embed import embed_query
    from nice_rag.search.hsk_index import search_hybrid

    hits = search_hybrid(query_text=query_text, query_vec=embed_query(query_text), limit=k)
    return [(h.hs_code, h.score) for h in hits]


def _top1(query_text: str) -> tuple[str | None, float]:
    hits = _topk(query_text, k=1)
    return hits[0] if hits else (None, 0.0)


def _top1_score(query_text: str) -> float:
    return _top1(query_text)[1]


def _cosine(a: list[float], b: list[float]) -> float:
    # embed_query 는 L2 정규화 벡터를 반환하므로 내적 = 코사인
    return sum(x * y for x, y in zip(a, b, strict=True))


# 의미 가드: 후보가 원질의와 의미적으로 무관(LLM 환각)하면 탈락
_SEMANTIC_MIN_COS = 0.5


def learn(*, limit: int = 20, dry_run: bool = False) -> int:
    from sqlalchemy import text

    from nice_poc.db import get_pg_engine
    from nice_rag.config import get_rag_settings
    from nice_rag.search.normalize import normalize_query

    s = get_rag_settings()
    engine = get_pg_engine()

    with engine.connect() as c:
        rows = c.execute(
            text(
                """
                SELECT DISTINCT ON (query) query
                FROM rag.search_log
                WHERE low_confidence AND NOT resolved
                ORDER BY query, created_at DESC
                LIMIT :n
                """
            ),
            {"n": limit},
        ).fetchall()
    queries = [r[0] for r in rows]
    print(f"미해결 저신뢰 질의: {len(queries)}건 (검증 임계치 {s.syn_verify_threshold})")

    from nice_rag.search.hsk_embed import embed_query
    from nice_rag.search.synonyms import expand_query

    registered = 0
    for q in queries:
        # 운영 동작과 동일한 baseline — 기존(빌트인+학습) 동의어 확장 포함.
        # 그래야 "이미 해결된 질의"에 중복 동의어를 등록하지 않는다.
        q_norm = expand_query(q)
        _, baseline = _top1(q_norm)
        q_vec = embed_query(q_norm)
        best: tuple[str, float] | None = None
        try:
            candidates = _gen_candidates(q)
        except Exception as exc:  # noqa: BLE001
            print(f"  [skip] {q!r} — LLM 실패: {exc.__class__.__name__}")
            continue

        for cand in candidates:
            cand_norm = normalize_query(cand)
            if not cand_norm or cand_norm in q_norm:
                continue
            # 가드 1 — 의미 관련성: LLM 환각 후보 차단
            cos = _cosine(q_vec, embed_query(cand_norm))
            if cos < _SEMANTIC_MIN_COS:
                print(f"    · {cand!r} 탈락 (의미 cos {cos:.2f})")
                continue
            # 가드 2 — 수렴: 결합 검색과 후보 단독 검색의 top3 가 호(hs4) 레벨에서
            # 겹쳐야 (후보가 검색을 일관된 품목군으로 끌고 간다 = 색인 용어 도달).
            # HS 는 계층 구조라 정확-코드 비교는 과도하게 엄격 — 소호 차이로
            # 정답 후보가 탈락한다 (예: 2805.30 희토류 vs 2805.40).
            comb = _topk(f"{q_norm} {cand_norm}")
            alone = _topk(cand_norm)
            comb_hs4 = {c[:4] for c, _ in comb}
            alone_hs4 = {c[:4] for c, _ in alone}
            if not (comb_hs4 & alone_hs4):
                print(f"    · {cand!r} 탈락 (수렴 실패 {sorted(comb_hs4)}≁{sorted(alone_hs4)})")
                continue
            comb_score = comb[0][1] if comb else 0.0
            # 가드 3 — 개선: ① 절대 임계치 통과 또는 ② baseline 대비 50% 이상
            # 대폭 개선 (단, 단일시그널 1위 0.0164 는 넘어야). 절대 기준만 쓰면
            # baseline 이 바닥인 질의의 큰 개선(0.0164→0.0286 등)을 놓친다.
            improved = comb_score > baseline and (
                comb_score >= s.syn_verify_threshold
                or (comb_score >= baseline * 1.5 and comb_score > 0.0165)
            )
            if not improved:
                print(
                    f"    · {cand!r} 탈락 (결합 {comb_score:.4f}, "
                    f"baseline {baseline:.4f}, 임계 {s.syn_verify_threshold})"
                )
                continue
            if best is None or comb_score > best[1]:
                best = (cand_norm, comb_score)

        if best is None:
            # 미등록 질의는 resolved 하지 않음 — 다음 배치에서 temperature
            # 샘플링으로 다른 후보를 재시도 (등록 성공 시에만 큐에서 제거)
            print(f"  [미등록] {q!r} baseline={baseline:.4f} — 검증 통과 후보 없음 (재시도 대상)")
            continue
        else:
            print(f"  [등록] {q_norm!r} → {best[0]!r} ({baseline:.4f} → {best[1]:.4f})")
            registered += 1
            if not dry_run:
                with engine.begin() as c:
                    c.execute(
                        text(
                            """
                            INSERT INTO rag.synonyms (alias, expansion, source, verified_score)
                            VALUES (:a, :e, 'auto', :sc)
                            ON CONFLICT (alias) DO UPDATE
                                SET expansion = EXCLUDED.expansion,
                                    verified_score = EXCLUDED.verified_score,
                                    enabled = true
                            """
                        ),
                        {"a": q_norm, "e": best[0], "sc": best[1]},
                    )

        if not dry_run:
            with engine.begin() as c:
                c.execute(
                    text("UPDATE rag.search_log SET resolved = true WHERE query = :q"),
                    {"q": q},
                )

    print(f"\n등록 {registered}건 / 처리 {len(queries)}건{' (dry-run)' if dry_run else ''}")
    return 0


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--limit", type=int, default=20, help="이번 실행에서 처리할 질의 수")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="등록/resolved 마킹 없이 후보 생성·검증만",
    )


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return learn(limit=args.limit, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001
        print(f"synonym learn failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
