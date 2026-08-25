"""nice_migrate.cri — company_edge rate → cri2 누적망 점수 → company_credit_cri 기록.

DB 의존을 피하려 in-memory SQLite 에 public 스키마를 부착해 격리(test_edge_aggregate
패턴). 기대값은 cri2 스펙 5노드 샘플(A~E) — tests/test_nice_shock.py 의
test_cri_matches_spec 및 nice_shock _EX_CRI_RESP 와 동일 값.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from nice_migrate.cri import update_cri_weights

# (from, to, sell_rate, buy_rate) — nice_shock _EX_CRI_REQ 의 sell/buy_share 그대로.
_EDGES_2024 = [
    ("A", "B", 0.300, 0.375),
    ("A", "D", 0.200, 0.333),
    ("D", "B", 0.300, 0.225),
    ("D", "E", 0.400, 0.600),
    ("B", "C", 0.500, 0.800),
    ("B", "A", 0.200, 0.160),
]
_GRADES_2024 = [("A", "AA"), ("B", "NR"), ("C", "BBB"), ("D", "A"), ("E", "BB")]


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("ATTACH DATABASE ':memory:' AS public"))
        c.execute(text(
            "CREATE TABLE public.company_edge ("
            "from_bizno TEXT, to_bizno TEXT, trade_year TEXT, "
            "sell_rate REAL, buy_rate REAL)"
        ))
        c.execute(text(
            "CREATE TABLE public.company_credit_cri ("
            "bizno TEXT, grd_st_year TEXT, crigrd TEXT, bat_seq INTEGER, "
            "weight_sell_avg REAL, weight_buy_avg REAL)"
        ))
        c.execute(
            text("INSERT INTO company_edge VALUES (:f,:t,'2024',:s,:b)"),
            [{"f": f, "t": t, "s": s, "b": b} for f, t, s, b in _EDGES_2024],
        )
        # rate 미채움(NULL) 연도 — update_trade_rate 선행 없이는 건너뛰는지 확인용.
        c.execute(text(
            "INSERT INTO company_edge VALUES ('A','B','2023',NULL,NULL)"
        ))
        c.execute(
            text("INSERT INTO company_credit_cri VALUES (:b,'2024',:g,1,NULL,NULL)"),
            [{"b": b, "g": g} for b, g in _GRADES_2024],
        )
    return eng


def _weights(engine) -> dict[str, tuple]:
    with engine.connect() as c:
        rows = c.execute(text(
            "SELECT bizno, weight_sell_avg, weight_buy_avg "
            "FROM public.company_credit_cri WHERE grd_st_year='2024'"
        )).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def test_scores_match_cri2_spec(engine) -> None:
    """기록된 weight_sell/buy_avg 가 cri2 스펙 샘플 값과 일치."""
    stats = update_cri_weights(engine, year="2024")
    w = _weights(engine)
    assert abs(w["A"][0] - 3.739130) < 1e-5   # A 누적판매망 점수
    assert abs(w["A"][1] - 3.0) < 1e-5        # A 누적구매망 점수
    assert w["C"][0] is None                  # C 판매 엣지 없음 → NULL
    assert abs(w["C"][1] - 2.333370) < 1e-5
    assert abs(w["D"][0] - 4.436860) < 1e-5
    assert abs(w["E"][1] - 2.738413) < 1e-5
    st = stats["per_year"]["2024"]
    assert st["nodes"] == 5 and st["edges"] == 6
    assert st["graded_nodes"] == 4            # NR(B) 는 무등급
    assert st["rows_updated"] == 5            # 점수 NULL 이어도 행은 갱신


def test_dry_run_writes_nothing(engine) -> None:
    stats = update_cri_weights(engine, year="2024", dry_run=True)
    assert stats["per_year"]["2024"]["rows_updated"] == 0
    assert all(v == (None, None) for v in _weights(engine).values())


def test_year_matching_and_rate_guard(engine) -> None:
    """연도 자동 결정: 등급 행 없는 2023 은 skip 목록, rate NULL 엣지는 계산 제외."""
    stats = update_cri_weights(engine)          # year 미지정 → 교집합
    assert stats["years"] == ["2024"]
    assert stats["years_skipped"] == ["2023"]   # 등급 행 없음 → 기록 불가
    # 2023 을 강제 지정하면 rate 채워진 엣지가 없어 0 처리(예외 아님)
    stats2 = update_cri_weights(engine, year="2023")
    assert stats2["per_year"]["2023"] == {"nodes": 0, "edges": 0, "rows_updated": 0}


def test_node_without_grade_row_skipped(engine) -> None:
    """등급 테이블에 행이 없는 노드는 기록 대상에서 제외(통계로 보고)."""
    with engine.begin() as c:
        c.execute(text(
            "DELETE FROM public.company_credit_cri WHERE bizno='E'"
        ))
    stats = update_cri_weights(engine, year="2024")
    st = stats["per_year"]["2024"]
    assert st["nodes_without_grade_row"] == 1
    assert st["rows_updated"] == 4
    assert "E" not in _weights(engine)
