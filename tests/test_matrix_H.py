"""구현명세서 §9.1 test_matrix_H.py — H 열합 ≤ 1, 분모 우선순위, 연도 필터."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nice_poc.matrix import matrix_H


def _firms(**denoms: dict[str, float]) -> pd.DataFrame:
    base = pd.DataFrame(index=pd.Index(["A", "B", "C"], name="firm_id"))
    for col, mapping in denoms.items():
        base[col] = base.index.map(mapping).astype("float64")
    return base


def _edges(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["source_id", "target_id", "year", "amount", "target_cate"],
    )


def test_year_filter() -> None:
    firms = _firms(sales_year_fin={"A": 1000, "B": 1000, "C": 1000})
    edges = _edges(
        [
            ("A", "B", 2023, 100, "일반"),
            ("A", "B", 2024, 300, "일반"),
        ]
    )

    h = matrix_H.build(edges, firms, year=2024)

    assert h.H.shape == (3, 3)
    # 2024 만 반영: H[A→B] = 300/1000
    a, b = list(firms.index).index("A"), list(firms.index).index("B")
    np.testing.assert_allclose(h.H[a, b], 0.3)


def test_b2c_gov_excluded() -> None:
    firms = _firms(sales_year_fin={"A": 1000, "B": 1000, "C": 1000})
    edges = _edges(
        [
            ("A", "B", 2024, 100, "일반"),
            ("A", "B", 2024, 200, "B2C"),
            ("A", "B", 2024, 400, "GOV"),
        ]
    )

    h = matrix_H.build(edges, firms, year=2024)

    a, b = 0, 1
    np.testing.assert_allclose(h.H[a, b], 0.1)


def test_denominator_priority_fin_overrides_vat() -> None:
    firms = _firms(
        sales_year_fin={"A": np.nan, "B": 5000, "C": np.nan},
        vat_fs_est_sales={"A": 1000, "B": 1000, "C": np.nan},
    )
    edges = _edges(
        [
            ("A", "B", 2024, 500, "일반"),
            ("A", "C", 2024, 100, "일반"),
        ]
    )

    h = matrix_H.build(edges, firms, year=2024)

    np.testing.assert_allclose(h.denominators.loc["B"], 5000)
    np.testing.assert_allclose(h.denominators.loc["A"], 1000)
    assert np.isnan(h.denominators.loc["C"])
    # C 는 분모 없으므로 H[A,C] 는 0
    a, c = 0, 2
    np.testing.assert_allclose(h.H[a, c], 0.0)


def test_col_sum_normalized_when_over_one() -> None:
    firms = _firms(sales_year_fin={"A": 1000, "B": 1000, "C": 100})
    # C 로 들어오는 분자합 = 50+80 = 130 > 분모 100 → 열 정규화
    edges = _edges(
        [
            ("A", "C", 2024, 50, "일반"),
            ("B", "C", 2024, 80, "일반"),
            ("A", "B", 2024, 200, "일반"),
        ]
    )

    h = matrix_H.build(edges, firms, year=2024)

    col_sum = np.asarray(h.H.sum(axis=0)).ravel()
    assert (col_sum <= 1.0 + 1e-12).all()
    # C 열의 비율은 보존
    c = 2
    np.testing.assert_allclose(h.H[0, c] / h.H[1, c], 50 / 80)
