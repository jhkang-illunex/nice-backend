"""회사·거래내역 CSV → 누적 판매망/구매망 CRI 등급 산출 (DB 미사용, 순수 stdlib).

원본: NICE 제공 공급망 CRI 샘플 구현(구 ``nice_shock/cri2.py`` — 여기로 이관)을
함수화하고 CSV 입출력으로 래핑. 알고리즘 해석은 원본 주석을 따른다:
  - 회사1 -> 회사2 = 회사1(판매자)이 회사2(구매자)에게 판매
  - 거래비중 = 거래금액 / 회사1(판매자) 거래총금액
  - 구매망 가중치 = 거래금액 / 구매자 거래총금액 (분모도 "내 총금액")
  - 누적 거래망 T = W + W² + W³ + … (loop 는 전파에 포함, 최종 집계에서
    자기 자신 기여분만 제외)
  - R/NR 등 무등급은 점수 계산에서 제외 (Coverage 하락으로만 반영)

입력 CSV (utf-8/utf-8-sig/cp949 자동 판별, 헤더 별칭 허용):
  회사용:   회사(=company|id), 신용등급(=grade), 거래총금액(=총금액|sales|amount)
  거래내역: 회사1(=seller|source), 회사2(=buyer|target), 거래비중(=비중|share|rate)

출력 CSV 컬럼: 회사1, 누적판매망등급, 누적판매망등급점수, 누적구매망등급, 누적구매망등급점수
  해당 방향 거래가 없거나 유효 등급 거래처가 없으면 등급 '-', 점수 빈칸.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# 등급 → 점수 (클수록 위험). R/NR 등 미매핑은 무등급(유효 제외).
GRADE_SCORE: dict[str, int] = {
    "AAA": 1, "AA": 2, "A": 3, "BBB": 4, "BB": 5,
    "B": 6, "CCC": 7, "CC": 8, "C": 9, "D": 10,
}


def grade_to_score(grade) -> int | None:
    """노치 제거(영문자만 남김) 후 등급→점수. 매핑 안 되면 None(무등급)."""
    if grade is None:
        return None
    base = re.sub(r"[^A-Za-z]", "", str(grade)).upper()
    return GRADE_SCORE.get(base)


def score_to_grade(avg_score) -> str:
    """평균 점수를 표시용 등급으로 역변환 (0.5 간격 구간)."""
    if avg_score is None:
        return "-"
    bounds = [
        (1.5, "AAA"), (2.5, "AA"), (3.5, "A"), (4.5, "BBB"), (5.5, "BB"),
        (6.5, "B"), (7.5, "CCC"), (8.5, "CC"), (9.5, "C"),
    ]
    for upper, grade in bounds:
        if avg_score < upper:
            return grade
    return "D"


# ── 행렬 연산 (dict-of-dict) — 원본 cri2 구현 이관 ─────────────────────────────


def _empty_matrix(nodes: list[str]) -> dict[str, dict[str, float]]:
    return {i: {j: 0.0 for j in nodes} for i in nodes}


def _matmul(a, b, nodes):
    """A @ B. self-return 을 제거하지 않아 A→B→A→… loop 경로가 자연 누적된다."""
    c = _empty_matrix(nodes)
    for i in nodes:
        for k in nodes:
            if a[i][k] == 0:
                continue
            for j in nodes:
                if b[k][j] == 0:
                    continue
                c[i][j] += a[i][k] * b[k][j]
    return c


def _cumulative(w, nodes, *, epsilon: float = 1e-8, max_iter: int = 1000):
    """누적 거래망 T = W + W² + W³ + … (단계 전파량 < epsilon 이면 수렴 종료)."""
    total = _empty_matrix(nodes)
    current = {i: dict(w[i]) for i in nodes}
    for _ in range(max_iter):
        if sum(abs(current[i][j]) for i in nodes for j in nodes) < epsilon:
            break
        for i in nodes:
            for j in nodes:
                total[i][j] += current[i][j]
        current = _matmul(current, w, nodes)
    return total


def build_matrices(companies: dict[str, dict], edges: list[tuple[str, str, float]]):
    """판매망 S·구매망 P 행렬 생성.

    S[판매][구매] = 거래비중(판매자 총금액 대비).
    P[구매][판매] = 거래금액 / 구매자 총금액  (거래금액 = 판매자 총금액 × 거래비중).
    """
    nodes = list(companies)
    s = _empty_matrix(nodes)
    p = _empty_matrix(nodes)
    for seller, buyer, share in edges:
        seller_sales = float(companies[seller]["sales"])
        buyer_sales = float(companies[buyer]["sales"])
        if buyer_sales <= 0:
            raise ValueError(
                f"구매자 {buyer!r} 의 거래총금액이 0 이하 — 구매망 가중치(분모) 계산 불가"
            )
        amount = seller_sales * share
        s[seller][buyer] += share
        p[buyer][seller] += amount / buyer_sales
    return s, p


def _grade_of(m, i, nodes, score_by_id) -> tuple[str, float | None]:
    """행렬 M 의 i 행에서 (표시등급, 평균점수). 자기 자신 기여분은 제외."""
    valid_w = wscore = 0.0
    for j in nodes:
        if i == j or m[i][j] == 0:
            continue
        sc = score_by_id.get(j)
        if sc is not None:
            valid_w += m[i][j]
            wscore += m[i][j] * sc
    if valid_w <= 0:
        return "-", None
    avg = wscore / valid_w
    return score_to_grade(avg), avg


def cumulative_scores(
    s: dict[str, dict[str, float]],
    p: dict[str, dict[str, float]],
    nodes: list[str],
    score_by_id: dict[str, int | None],
) -> dict[str, dict]:
    """판매망 S·구매망 P 행렬에서 회사별 누적망 등급·점수 — cri2 핵심 (행렬 입력 공용 API).

    행렬을 이미 갖고 있는 호출자(예: nice_migrate.cri 의 DB sell_rate/buy_rate)가
    sales 유도(build_matrices) 없이 core 만 재사용할 수 있게 분리.
    """
    cum_s = _cumulative(s, nodes)
    cum_p = _cumulative(p, nodes)
    out: dict[str, dict] = {}
    for i in nodes:
        sg, ss = _grade_of(cum_s, i, nodes, score_by_id)
        bg, bs = _grade_of(cum_p, i, nodes, score_by_id)
        out[i] = {"sell_grade": sg, "sell_score": ss, "buy_grade": bg, "buy_score": bs}
    return out


def compute_cumulative_cri(
    companies: dict[str, dict], edges: list[tuple[str, str, float]]
) -> dict[str, dict]:
    """회사별 {sell_grade, sell_score, buy_grade, buy_score} (누적망 기준)."""
    nodes = list(companies)
    score_by_id = {i: grade_to_score(companies[i].get("grade")) for i in nodes}
    s, p = build_matrices(companies, edges)
    return cumulative_scores(s, p, nodes, score_by_id)


# ── CSV 입출력 ────────────────────────────────────────────────────────────────

_COMPANY_ALIASES = {
    "id": ("회사", "회사1", "company", "id", "기업", "bizno"),
    "grade": ("신용등급", "grade", "등급"),
    "sales": ("거래총금액", "총금액", "sales", "amount", "매출액"),
}
_EDGE_ALIASES = {
    "seller": ("회사1", "seller", "source", "판매자"),
    "buyer": ("회사2", "buyer", "target", "구매자"),
    "share": ("거래비중", "비중", "share", "rate", "sales_share"),
}


def _read_rows(path: Path) -> list[dict]:
    last_err: Exception | None = None
    for enc in ("utf-8-sig", "cp949"):
        try:
            with path.open(newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError as exc:
            last_err = exc
    raise ValueError(f"{path}: utf-8/cp949 어느 쪽으로도 읽지 못함") from last_err


def _pick(row: dict, aliases: tuple[str, ...], path: Path):
    for key in aliases:
        if key in row and str(row[key]).strip() != "":
            return str(row[key]).strip()
    raise ValueError(f"{path}: 컬럼 {aliases} 중 하나가 필요 (헤더: {list(row)})")


def read_companies(path: Path) -> dict[str, dict]:
    companies: dict[str, dict] = {}
    for row in _read_rows(path):
        cid = _pick(row, _COMPANY_ALIASES["id"], path)
        if cid in companies:
            raise ValueError(f"{path}: 회사 {cid!r} 중복 행")
        companies[cid] = {
            "grade": _pick(row, _COMPANY_ALIASES["grade"], path),
            "sales": float(_pick(row, _COMPANY_ALIASES["sales"], path)),
        }
    if not companies:
        raise ValueError(f"{path}: 회사 데이터가 비어 있음")
    return companies


def read_edges(path: Path, companies: dict[str, dict]) -> list[tuple[str, str, float]]:
    edges: list[tuple[str, str, float]] = []
    for row in _read_rows(path):
        seller = _pick(row, _EDGE_ALIASES["seller"], path)
        buyer = _pick(row, _EDGE_ALIASES["buyer"], path)
        share = float(_pick(row, _EDGE_ALIASES["share"], path))
        unknown = [c for c in (seller, buyer) if c not in companies]
        if unknown:
            raise ValueError(f"{path}: 회사용 CSV 에 없는 회사 {unknown} (행: {row})")
        edges.append((seller, buyer, share))
    if not edges:
        raise ValueError(f"{path}: 거래내역이 비어 있음")
    return edges


_OUT_HEADER = ["회사1", "누적판매망등급", "누적판매망등급점수", "누적구매망등급", "누적구매망등급점수"]


def _out_rows(result: dict[str, dict]) -> list[list[str]]:
    rows = []
    for cid, r in result.items():
        rows.append([
            cid,
            r["sell_grade"],
            f"{r['sell_score']:.4f}" if r["sell_score"] is not None else "",
            r["buy_grade"],
            f"{r['buy_score']:.4f}" if r["buy_score"] is not None else "",
        ])
    return rows


# ── 파이프라인 진입점 ─────────────────────────────────────────────────────────


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--companies", required=True, type=Path,
                        help="회사용 CSV — 회사, 신용등급, 거래총금액")
    parser.add_argument("--edges", required=True, type=Path,
                        help="거래내역 CSV — 회사1(판매자), 회사2(구매자), 거래비중(회사1 총금액 대비)")
    parser.add_argument("--out", type=Path, default=None,
                        help="결과 CSV 경로 (생략 시 stdout 표만 출력)")


def run(ns: argparse.Namespace) -> int:
    companies = read_companies(ns.companies)
    edges = read_edges(ns.edges, companies)
    result = compute_cumulative_cri(companies, edges)

    rows = _out_rows(result)
    widths = [max(len(h), *(len(r[k]) for r in rows)) for k, h in enumerate(_OUT_HEADER)]
    print("  ".join(h.ljust(w) for h, w in zip(_OUT_HEADER, widths, strict=True)))
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)))

    if ns.out is not None:
        with ns.out.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(_OUT_HEADER)
            writer.writerows(rows)
        log.info("cri: %d개 회사 → %s", len(rows), ns.out)
        print(f"\n저장: {ns.out} ({len(rows)}개 회사)")
    return 0
