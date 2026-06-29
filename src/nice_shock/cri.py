"""CRI(신용위험지표) 계산 — DB 의존 없음 (순수 함수).

입력:
  nodes : [{"id", "grade"|"score", "sales"}]
          grade = 신용등급(AAA~D, NR 등; 노치 포함 가능). score(1=AAA … 10=D) 직접 지정 가능.
          무등급(NR/R/매핑불가)은 전파엔 포함되나 CRI 점수 계산에선 제외.
  edges : [{"source", "target", "sell_share", "buy_share"}]
          source가 target에게 판매. sell_share=거래액/셀러매출, buy_share=거래액/바이어매출.

누적 거래망 = T = Σ_k λ^k W^k (직접 + 간접 + loop). 자기복귀(self-return)는 전파 포함·CRI 제외.
판매망(sell)·구매망(buy) 각각 기업별 지표 + 네트워크 Risk Index 산출.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence

# 등급 → 점수 (클수록 위험). NR/R 등 미매핑은 무등급(유효 제외).
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


def _propagate(
    graph: Mapping[str, list[tuple[str, float]]],
    *, lamb: float = 1.0, epsilon: float = 1e-9, max_iter: int = 1000,
) -> dict[tuple[str, str], float]:
    """T = W + λW² + λ²W³ + … 을 edge-list로 누적. (src,dst)별 모든 경로 가중치 합."""
    current: dict[tuple[str, str], float] = defaultdict(float)
    for src in graph:
        for dst, w in graph[src]:
            current[(src, dst)] += w
    total: dict[tuple[str, str], float] = defaultdict(float)
    for _ in range(max_iter):
        if sum(abs(v) for v in current.values()) < epsilon:
            break
        for k, v in current.items():
            total[k] += v
        nxt: dict[tuple[str, str], float] = defaultdict(float)
        for (src, mid), pw in current.items():
            for dst, ew in graph.get(mid, ()):  # type: ignore[call-overload]
                nxt[(src, dst)] += pw * ew * lamb
        current = nxt
    return dict(total)


def _company_metrics(
    total_paths: Mapping[tuple[str, str], float],
    node_ids: Sequence[str],
    score_by_id: Mapping[str, int | None],
) -> dict[str, dict]:
    """기업별 지표 — self-return(dst==i) 제외, 무등급(score None) 은 유효에서 제외."""
    out: dict[str, dict] = {}
    for i in node_ids:
        total_w = valid_w = wscore = 0.0
        for (src, dst), w in total_paths.items():
            if src != i or dst == i:  # 자기 출발 아님 / 자기복귀 → 제외
                continue
            total_w += w
            sc = score_by_id.get(dst)
            if sc is not None:  # 유효 등급만 가중평균에 반영
                valid_w += w
                wscore += w * sc
        out[i] = {
            "total_weight": total_w,
            "valid_weight": valid_w,
            "coverage": (valid_w / total_w) if total_w > 0 else None,
            "avg_cri": (wscore / valid_w) if valid_w > 0 else None,
            "exposure": wscore,
        }
    return out


def _network_index(
    metrics: Mapping[str, dict], sales_by_id: Mapping[str, float]
) -> dict:
    """매출 가중 네트워크 지표. RiskIndex = Σ(매출·exposure)/Σ(매출·유효비중)."""
    num = den = covden = 0.0
    for i, m in metrics.items():
        s = sales_by_id.get(i, 0.0)
        num += s * m["exposure"]
        den += s * m["valid_weight"]
        covden += s * m["total_weight"]
    return {
        "risk_index": (num / den) if den > 0 else None,
        "coverage": (den / covden) if covden > 0 else None,
    }


def compute_cri(
    nodes: Sequence[Mapping], edges: Sequence[Mapping], *, lamb: float = 1.0
) -> dict:
    """nodes·edges → 판매망/구매망 기업별 지표 + 네트워크 지표.

    반환: {"nodes": {id: {"sell": metrics, "buy": metrics}}, "network": {"sell":…, "buy":…}}
    """
    node_ids: list[str] = []
    score_by_id: dict[str, int | None] = {}
    sales_by_id: dict[str, float] = {}
    for n in nodes:
        nid = str(n.get("id", n.get("node_id")))
        node_ids.append(nid)
        sc = n.get("score")
        score_by_id[nid] = int(sc) if sc is not None else grade_to_score(n.get("grade"))
        sales_by_id[nid] = float(n.get("sales") or 0.0)

    sell_graph: dict[str, list[tuple[str, float]]] = defaultdict(list)
    buy_graph: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in edges:
        s, t = str(e["source"]), str(e["target"])
        sell_graph[s].append((t, float(e["sell_share"])))  # 판매망: 셀러→바이어
        buy_graph[t].append((s, float(e["buy_share"])))     # 구매망: 바이어→셀러

    sell_m = _company_metrics(_propagate(sell_graph, lamb=lamb), node_ids, score_by_id)
    buy_m = _company_metrics(_propagate(buy_graph, lamb=lamb), node_ids, score_by_id)
    return {
        "nodes": {i: {"sell": sell_m[i], "buy": buy_m[i]} for i in node_ids},
        "network": {
            "sell": _network_index(sell_m, sales_by_id),
            "buy": _network_index(buy_m, sales_by_id),
        },
    }
