"""edge_aggregate — 분기 신고 → 연단위 엣지 집계 정책 검증.

DB 의존을 피하려 in-memory SQLite 에 origin_itg_vat_dat 를 흉내낸 미니 테이블을
만들고 engine 주입으로 격리한다. (SQLite 3.25+ 윈도우 함수 사용)
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from nice_graph.shock.edge_aggregate import (
    AmendmentPolicy,
    aggregate_yearly_edges,
)

# (bizno, trs_obj, vat_stmt_yr, st_date, end_date, chasu, slyvl)
_ROWS = [
    # A→B 2024: 두 분기(1차) + 한 분기에 2차 수정신고가 1차와 공존
    ("A", "B", "2024", "20240101", "20240331", "1", 100.0),
    ("A", "B", "2024", "20240401", "20240630", "1", 200.0),
    ("A", "B", "2024", "20240401", "20240630", "2", 50.0),   # 동일분기 2차(수정)
    # A→C 2024: 1차만, 같은 분기 복수 세금계산서(중복 아님 → 합산 대상)
    ("A", "C", "2024", "20240101", "20240331", "1", 30.0),
    ("A", "C", "2024", "20240101", "20240331", "1", 70.0),
    # A→B 2025: 독립 2차(공존 1차 없음)
    ("A", "B", "2025", "20250101", "20250331", "2", 400.0),
]


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        # 운영 SQL 이 public.origin_itg_vat_dat 로 스키마 한정 → SQLite 에 public 부착.
        c.execute(text("ATTACH DATABASE ':memory:' AS public"))
        c.execute(
            text(
                "CREATE TABLE public.origin_itg_vat_dat ("
                "bizno TEXT, trs_obj_bizrregno TEXT, vat_stmt_yr TEXT, "
                "ttn_prid_st_date TEXT, ttn_prid_end_date TEXT, "
                "vat_phs_rnu_divcd TEXT, slyvl REAL)"
            )
        )
        c.execute(
            text(
                "INSERT INTO origin_itg_vat_dat VALUES "
                "(:b,:t,:y,:st,:en,:ch,:v)"
            ),
            [
                {"b": b, "t": t, "y": y, "st": st, "en": en, "ch": ch, "v": v}
                for (b, t, y, st, en, ch, v) in _ROWS
            ],
        )
    return eng


def _edge_map(edges):
    return {(e.from_bizno, e.to_bizno, e.trade_year): e for e in edges}


def test_sum_all_includes_every_row(engine):
    m = _edge_map(aggregate_yearly_edges(policy=AmendmentPolicy.SUM_ALL, engine=engine))
    # A→B 2024 = 100 + 200 + 50(2차도 합산) = 350
    assert m[("A", "B", "2024")].sly_amt == 350.0
    assert m[("A", "B", "2024")].n_filings == 3
    # A→C 2024 = 30 + 70 = 100 (동일분기 복수행 합산)
    assert m[("A", "C", "2024")].sly_amt == 100.0


def test_replace_drops_first_when_amendment_coexists(engine):
    m = _edge_map(aggregate_yearly_edges(policy=AmendmentPolicy.REPLACE, engine=engine))
    # A→B 2024: 1분기 100(1차 유지) + 2분기는 2차50 으로 대체(1차200 폐기) = 150
    assert m[("A", "B", "2024")].sly_amt == 150.0
    assert m[("A", "B", "2024")].n_filings == 2
    # A→C 2024: 2차 없음 → 100 그대로
    assert m[("A", "C", "2024")].sly_amt == 100.0
    # A→B 2025: 독립 2차 400 → 그대로 채택
    assert m[("A", "B", "2025")].sly_amt == 400.0


def test_first_only_ignores_amendments(engine):
    m = _edge_map(aggregate_yearly_edges(policy=AmendmentPolicy.FIRST_ONLY, engine=engine))
    # A→B 2024: 1차만 = 100 + 200 = 300
    assert m[("A", "B", "2024")].sly_amt == 300.0
    # A→B 2025: 1차 없음 → 엣지 자체가 없어야
    assert ("A", "B", "2025") not in m


def test_trade_year_filter(engine):
    e = aggregate_yearly_edges(
        policy=AmendmentPolicy.REPLACE, trade_year="2025", engine=engine
    )
    assert {x.trade_year for x in e} == {"2025"}


def test_normalize_source_rate_sums_to_one(engine):
    e = aggregate_yearly_edges(
        policy=AmendmentPolicy.REPLACE, normalize=True, engine=engine
    )
    # A 의 2024 outgoing: A→B 150 + A→C 100 = 250 → 비중 0.6 / 0.4
    m = _edge_map(e)
    assert m[("A", "B", "2024")].rate == pytest.approx(150 / 250)
    assert m[("A", "C", "2024")].rate == pytest.approx(100 / 250)
    # 같은 (from, year) 비중 합 = 1
    by_src: dict[tuple[str, str], float] = {}
    for x in e:
        by_src[(x.from_bizno, x.trade_year)] = by_src.get(
            (x.from_bizno, x.trade_year), 0.0
        ) + (x.rate or 0.0)
    for s in by_src.values():
        assert s == pytest.approx(1.0)


def test_normalize_off_leaves_rate_none(engine):
    e = aggregate_yearly_edges(policy=AmendmentPolicy.REPLACE, engine=engine)
    assert all(x.rate is None for x in e)
