"""회사·거래내역 CSV → 누적 판매망/구매망 CRI 등급 산출 (DB 미사용, stdlib+numpy).

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

import numpy as np

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


# ── 희소(sparse) 누적망 엔진 — numpy mat-vec (2026-08-28 대규모 대응 재작성) ────
# 원본 cri2 의 dense dict 행렬(O(N²) 메모리 · O(N³) 파이썬 곱)은 대규모에서 불가
# (실사례 2026-08-28: 노드 5백만·엣지 4백만 입력에서 400GB+ 점유 후에도 미종료).
# 최종 지표는 노드별 (유효 가중합, 가중 점수합) 두 값뿐이므로 누적행렬
# T = W + W² + … 를 만들지 않고 벡터 반복  x ← W·x  누적(T·v, O(E)/회)으로 계산한다.
# 자기 자신 기여 제외(원본 규칙, T_ii)는 순환(비자명 SCC·자기루프) 위의 등급 보유
# 노드에 한해서만 0 이 아니므로, SCC 부분그래프 열-배치로 정확 계산한다.
# 수렴 규칙(epsilon 컷오프·max_iter, 체크→누적→곱 순서)은 원본과 동일 — 소규모
# 회귀 테스트(cri2 스펙 5노드 샘플)가 dense 구현과의 값 일치를 보증한다.

DEFAULT_EPSILON = 1e-8
DEFAULT_MAX_ITER = 1000


def _matvec(n: int, rows, cols, w, x):
    """(W·x)_i = Σ_{(i,j,w)} w·x_j — 희소 엣지 배열 기반, O(E)."""
    if len(rows) == 0:
        return np.zeros(n)
    return np.bincount(rows, weights=w * x[cols], minlength=n)


def _accumulate(n, rows, cols, w, x0, *, epsilon: float, max_iter: int):
    """Σ_{k≥1} Wᵏ·x0 — 원본 _cumulative 와 동일한 종료 규칙(체크→누적→곱)."""
    total = np.zeros(n)
    cur = _matvec(n, rows, cols, w, x0)
    for _ in range(max_iter):
        if np.abs(cur).sum() < epsilon:
            break
        total += cur
        cur = _matvec(n, rows, cols, w, cur)
    return total


def _scc_labels(n: int, rows, cols) -> tuple[list[int], list[int]]:
    """반복형 Tarjan — 각 노드의 SCC id 와 SCC 크기. (역그래프도 SCC 동일)"""
    adj: list[list[int]] = [[] for _ in range(n)]
    for a, b in zip(rows.tolist(), cols.tolist(), strict=True):
        adj[a].append(b)
    index = [-1] * n
    low = [0] * n
    on_stack = [False] * n
    comp = [-1] * n
    stack: list[int] = []
    counter = 0
    n_comp = 0
    sizes: list[int] = []
    for root in range(n):
        if index[root] != -1:
            continue
        work = [(root, 0)]
        while work:
            v, pi = work[-1]
            if pi == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack[v] = True
            recurse = False
            for i in range(pi, len(adj[v])):
                u = adj[v][i]
                if index[u] == -1:
                    work[-1] = (v, i + 1)
                    work.append((u, 0))
                    recurse = True
                    break
                if on_stack[u]:
                    low[v] = min(low[v], index[u])
            if recurse:
                continue
            work.pop()
            if work:
                pv = work[-1][0]
                low[pv] = min(low[pv], low[v])
            if low[v] == index[v]:
                size = 0
                while True:
                    u = stack.pop()
                    on_stack[u] = False
                    comp[u] = n_comp
                    size += 1
                    if u == v:
                        break
                sizes.append(size)
                n_comp += 1
    return comp, sizes


def _diag_cumulative(
    n, rows, cols, w, targets, comp, comp_size, has_self_loop,
    *, epsilon: float, max_iter: int,
):
    """T_ii = Σ_k (Wᵏ)_ii — targets 노드만. i→…→i 보행은 i 의 SCC 안에 갇히므로
    비자명 SCC(크기≥2 또는 자기루프)별 부분그래프에서 열-배치로 계산.

    성능(2026-08-28 재작성 — 현장 실측 14분의 원인 두 겹 제거):
    1) 대상 SCC 내부 엣지를 **전역 1회 추출·압축** — SCC 마다 전체 엣지를 재스캔하지 않음.
    2) **wave-packing**: SCC 블록끼리는 내부-엣지 그래프에서 서로 도달 불가(분리 블록)
       이므로, 서로 다른 SCC 의 j 번째 타깃들을 한 벡터에 실어 bincount 반복 1회로 동시
       계산. 파이썬 루프 횟수가 "SCC 수"가 아니라 "SCC 당 최대 타깃 수"(대개 한 자리)에
       비례. 교차 오염 없음 — x0 의 서로 다른 SCC 성분은 영원히 자기 블록 안에 머문다.
    """
    d = np.zeros(n)
    comp_arr = np.asarray(comp)
    need = [t for t in targets if comp_size[comp[t]] >= 2 or has_self_loop[t]]
    if not need:
        return d
    by_comp: dict[int, list[int]] = {}
    for t in need:
        by_comp.setdefault(comp[t], []).append(t)
    need_comps = np.asarray(sorted(by_comp))
    # 대상 SCC 의 내부 엣지만 1회 추출 → 로컬 압축 인덱스(대상 SCC 노드만)
    e_comp = comp_arr[rows]
    keep = (e_comp == comp_arr[cols]) & np.isin(e_comp, need_comps)
    r_k, c_k, w_k = rows[keep], cols[keep], w[keep]
    local_nodes = np.unique(np.concatenate([r_k, c_k, np.asarray(need)]))
    nl = len(local_nodes)
    r_l = np.searchsorted(local_nodes, r_k)
    c_l = np.searchsorted(local_nodes, c_k)
    waves = max(len(ts) for ts in by_comp.values())
    log.info("cri diag: 대상 SCC %d개 / 내부 엣지 %d / 대상 노드 %d / wave %d회",
             len(by_comp), len(r_k), len(need), waves)
    for j in range(waves):
        pos = np.searchsorted(
            local_nodes, np.asarray([ts[j] for ts in by_comp.values() if len(ts) > j])
        )
        tgt = np.asarray([ts[j] for ts in by_comp.values() if len(ts) > j])
        cur = np.zeros(nl)
        cur[pos] = 1.0
        cur = _matvec(nl, r_l, c_l, w_k, cur)  # k=1 항 — 종료 규칙 _accumulate 동일
        acc = np.zeros(len(pos))
        for _ in range(max_iter):
            if np.abs(cur).sum() < epsilon:
                break
            acc += cur[pos]
            cur = _matvec(nl, r_l, c_l, w_k, cur)
        d[tgt] = acc
    return d


def cumulative_scores_from_edges(
    nodes: list[str],
    s_edges: list[tuple[str, str, float]],
    p_edges: list[tuple[str, str, float]],
    score_by_id: dict[str, int | None],
    *,
    epsilon: float = DEFAULT_EPSILON,
    max_iter: int = DEFAULT_MAX_ITER,
) -> dict[str, dict]:
    """엣지 리스트 입력의 cri2 core — 대규모 안전(O(N+E) 메모리).

    s_edges: (판매자, 구매자, 판매비중)  → S[판매][구매]
    p_edges: (구매자, 판매자, 구매가중)  → P[구매][판매]
    중복 엣지는 원본과 동일하게 합산. score_by_id 에 None/부재 = 무등급.
    """
    n = len(nodes)
    idx = {nid: i for i, nid in enumerate(nodes)}

    def arrays(edges):
        if not edges:
            z = np.zeros(0, dtype=np.int64)
            return z, z, np.zeros(0)
        r = np.fromiter((idx[a] for a, _, _ in edges), dtype=np.int64, count=len(edges))
        c = np.fromiter((idx[b] for _, b, _ in edges), dtype=np.int64, count=len(edges))
        wv = np.fromiter((float(v) for _, _, v in edges), dtype=np.float64, count=len(edges))
        return r, c, wv

    sr, sc, sw = arrays(s_edges)
    pr, pc, pw = arrays(p_edges)

    scores = np.zeros(n)
    graded = np.zeros(n)
    for nid, sc_val in score_by_id.items():
        if sc_val is not None and nid in idx:
            scores[idx[nid]] = float(sc_val)
            graded[idx[nid]] = 1.0
    u = scores * graded

    # SCC 는 방향 그래프와 그 역그래프에서 동일 — S 방향 구조로 1회 계산해 공용.
    comp, comp_size = _scc_labels(n, sr, sc)
    self_s = np.zeros(n, dtype=bool)
    self_s[sr[sr == sc]] = True
    self_p = np.zeros(n, dtype=bool)
    self_p[pr[pr == pc]] = True
    graded_idx = np.nonzero(graded)[0].tolist()

    out: dict[str, dict] = {}
    results = {}
    for key, (r, c, wv, selfloop) in {
        "sell": (sr, sc, sw, self_s), "buy": (pr, pc, pw, self_p),
    }.items():
        acc_u = _accumulate(n, r, c, wv, u, epsilon=epsilon, max_iter=max_iter)
        acc_v = _accumulate(n, r, c, wv, graded, epsilon=epsilon, max_iter=max_iter)
        d = _diag_cumulative(n, r, c, wv, graded_idx, comp, comp_size, selfloop,
                             epsilon=epsilon, max_iter=max_iter)
        results[key] = (acc_u - d * u, acc_v - d * graded)

    for i, nid in enumerate(nodes):
        row: dict = {}
        for key in ("sell", "buy"):
            wsc, wsum = results[key][0][i], results[key][1][i]
            if wsum > 0:
                avg = float(wsc / wsum)
                row[f"{key}_grade"], row[f"{key}_score"] = score_to_grade(avg), avg
            else:
                row[f"{key}_grade"], row[f"{key}_score"] = "-", None
        out[nid] = row
    return out


def cumulative_scores(
    s: dict[str, dict[str, float]],
    p: dict[str, dict[str, float]],
    nodes: list[str],
    score_by_id: dict[str, int | None],
) -> dict[str, dict]:
    """dict 행렬 입력 어댑터(하위호환) — 0 이 아닌 셀만 엣지로 변환해 sparse core 호출.

    대규모 데이터는 행렬 dict 구성 자체가 O(N²)라 이 어댑터 대신
    cumulative_scores_from_edges 를 직접 쓸 것 (nice_migrate.cri 는 그렇게 전환됨).
    """
    def to_edges(m):
        return [(i, j, v) for i, row in m.items() for j, v in row.items() if v != 0.0]

    return cumulative_scores_from_edges(nodes, to_edges(s), to_edges(p), score_by_id)


def compute_cumulative_cri(
    companies: dict[str, dict], edges: list[tuple[str, str, float]]
) -> dict[str, dict]:
    """회사별 {sell_grade, sell_score, buy_grade, buy_score} (누적망 기준).

    S[판매][구매] = 거래비중(판매자 총금액 대비) /
    P[구매][판매] = 거래금액 ÷ 구매자 총금액 (거래금액 = 판매자 총금액 × 거래비중).
    """
    nodes = list(companies)
    score_by_id = {i: grade_to_score(companies[i].get("grade")) for i in nodes}
    s_edges: list[tuple[str, str, float]] = []
    p_edges: list[tuple[str, str, float]] = []
    for seller, buyer, share in edges:
        buyer_sales = float(companies[buyer]["sales"])
        if buyer_sales <= 0:
            raise ValueError(
                f"구매자 {buyer!r} 의 거래총금액이 0 이하 — 구매망 가중치(분모) 계산 불가"
            )
        amount = float(companies[seller]["sales"]) * share
        s_edges.append((seller, buyer, share))
        p_edges.append((buyer, seller, amount / buyer_sales))
    return cumulative_scores_from_edges(nodes, s_edges, p_edges, score_by_id)


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
