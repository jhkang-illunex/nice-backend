"""구현명세서 §9.1 test_cap.py — max-delta cap 사후 |Δx| ≤ sales 보장."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nice_poc.safety import max_delta_cap


def test_cap_revenue_clamps_to_signed_limit() -> None:
    idx = pd.Index(["A", "B", "C", "D"], name="firm_id")
    delta = pd.Series([2_000_000, -3_000_000, 500_000, 0], index=idx, dtype="float64")
    sales = pd.Series([1_000_000, 1_500_000, 1_000_000, 100], index=idx, dtype="float64")

    capped, flag = max_delta_cap.cap_revenue(delta, sales)

    # |Δ| ≤ |sales| 보장
    assert (capped.abs() <= sales.abs() + 1e-9).all()
    # 부호 보존
    np.testing.assert_allclose(np.sign(capped[delta != 0]), np.sign(delta[delta != 0]))
    # 첫 두 행은 capped, 나머지는 통과
    assert flag.loc["A"] and flag.loc["B"]
    assert not flag.loc["C"] and not flag.loc["D"]
    # capped 값 정확
    np.testing.assert_allclose(capped.loc["A"], 1_000_000)
    np.testing.assert_allclose(capped.loc["B"], -1_500_000)


def test_capped_ratio() -> None:
    flag = pd.Series([True, True, False, False, True])
    np.testing.assert_allclose(max_delta_cap.capped_ratio(flag), 0.6)
    assert max_delta_cap.capped_ratio(pd.Series([], dtype=bool)) == 0.0


def test_cap_handles_missing_base() -> None:
    delta = pd.Series([500.0, 100.0], index=["A", "B"])
    base = pd.Series([200.0], index=["A"])  # B 누락 → 0 으로 처리
    capped, flag = max_delta_cap.cap_revenue(delta, base)
    np.testing.assert_allclose(capped.loc["A"], 200.0)
    np.testing.assert_allclose(capped.loc["B"], 0.0)
    assert flag.loc["B"]
