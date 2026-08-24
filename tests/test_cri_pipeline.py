"""nice_ingest cri 파이프라인 — CSV 입출력 + 누적 판매/구매망 CRI (DB 미사용).

기대값은 이관 전 원본(nice_shock/cri2.py 샘플 구현)을 동일 데이터로 실행한 결과.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from nice_ingest.pipelines.cri.pipeline import (
    compute_cumulative_cri,
    grade_to_score,
    read_companies,
    read_edges,
    run,
    score_to_grade,
)

_COMPANIES = {
    "A": {"grade": "AA", "sales": 1000.0},
    "B": {"grade": "BBB", "sales": 800.0},
    "C": {"grade": "NR", "sales": 500.0},
    "D": {"grade": "A", "sales": 600.0},
    "E": {"grade": "R", "sales": 400.0},
}
_EDGES = [
    ("A", "B", 0.3), ("A", "D", 0.2), ("D", "B", 0.3),
    ("D", "E", 0.4), ("B", "C", 0.5), ("B", "A", 0.2),
]


def test_cumulative_cri_matches_cri2_sample() -> None:
    """원본 cri2 샘플 실행값과 일치 — 누적 판매/구매망 등급·점수."""
    r = compute_cumulative_cri(_COMPANIES, _EDGES)
    assert (r["A"]["sell_grade"], round(r["A"]["sell_score"], 4)) == ("BBB", 3.6429)
    assert (r["A"]["buy_grade"], round(r["A"]["buy_score"], 4)) == ("BBB", 3.8163)
    assert (r["B"]["sell_grade"], round(r["B"]["sell_score"], 4)) == ("AA", 2.1667)
    assert (r["B"]["buy_grade"], round(r["B"]["buy_score"], 4)) == ("AA", 2.3333)
    assert r["C"]["sell_grade"] == "-" and r["C"]["sell_score"] is None  # 판매 엣지 없음
    assert (r["C"]["buy_grade"], round(r["C"]["buy_score"], 4)) == ("A", 3.3284)
    assert (r["D"]["sell_grade"], round(r["D"]["sell_score"], 4)) == ("BBB", 3.6667)
    assert (r["D"]["buy_grade"], round(r["D"]["buy_score"], 4)) == ("AA", 2.2759)
    assert r["E"]["sell_grade"] == "-"  # 판매 엣지 없음
    assert (r["E"]["buy_grade"], round(r["E"]["buy_score"], 4)) == ("A", 2.7889)


def test_grade_parsing_and_display() -> None:
    """노치 제거 매핑(cri.py 와 동일) + 점수→표시등급 역변환."""
    assert grade_to_score("AA-") == 2 and grade_to_score("BBB+") == 4
    assert grade_to_score("NR") is None and grade_to_score("R") is None
    assert score_to_grade(3.6429) == "BBB" and score_to_grade(None) == "-"
    assert score_to_grade(9.7) == "D"


def test_csv_roundtrip(tmp_path: Path) -> None:
    """한글 헤더 CSV 입력 → run() → 출력 CSV 5컬럼 스키마·값 검증."""
    comp = tmp_path / "companies.csv"
    comp.write_text(
        "회사,신용등급,거래총금액\nA,AA,1000\nB,BBB,800\nC,NR,500\nD,A,600\nE,R,400\n",
        encoding="utf-8",
    )
    edge = tmp_path / "edges.csv"
    edge.write_text(
        "회사1,회사2,거래비중\nA,B,0.3\nA,D,0.2\nD,B,0.3\nD,E,0.4\nB,C,0.5\nB,A,0.2\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.csv"
    assert run(argparse.Namespace(companies=comp, edges=edge, out=out)) == 0

    with out.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    by_id = {r["회사1"]: r for r in rows}
    assert list(rows[0]) == ["회사1", "누적판매망등급", "누적판매망등급점수", "누적구매망등급", "누적구매망등급점수"]
    assert by_id["A"]["누적판매망등급"] == "BBB" and by_id["A"]["누적판매망등급점수"] == "3.6429"
    assert by_id["C"]["누적판매망등급"] == "-" and by_id["C"]["누적판매망등급점수"] == ""
    assert by_id["E"]["누적구매망등급"] == "A" and by_id["E"]["누적구매망등급점수"] == "2.7889"


def test_unknown_company_in_edges_rejected(tmp_path: Path) -> None:
    """거래내역에 회사용 CSV 미등록 회사가 있으면 명시적 오류."""
    import pytest

    comp = tmp_path / "c.csv"
    comp.write_text("회사,신용등급,거래총금액\nA,AA,1000\n", encoding="utf-8")
    edge = tmp_path / "e.csv"
    edge.write_text("회사1,회사2,거래비중\nA,Z,0.3\n", encoding="utf-8")
    companies = read_companies(comp)
    with pytest.raises(ValueError, match="없는 회사"):
        read_edges(edge, companies)
