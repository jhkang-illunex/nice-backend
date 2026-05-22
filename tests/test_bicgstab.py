"""구현명세서 §9.1 test_bicgstab.py — 직접 역행렬 vs BiCGSTAB 일치 (소규모)."""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from nice_poc.propagation import bicgstab, leontief


def test_bicgstab_matches_dense_inverse() -> None:
    rng = np.random.default_rng(0)
    n = 30
    M = rng.uniform(0, 0.05, size=(n, n))
    np.fill_diagonal(M, 0.0)
    b = rng.uniform(-1.0, 1.0, size=n)

    expected = np.linalg.solve(np.eye(n) - M, b)
    got = bicgstab.solve(sp.csr_matrix(M), b)

    np.testing.assert_allclose(got, expected, rtol=1e-5, atol=1e-8)


def test_demand_split_sums_to_total() -> None:
    rng = np.random.default_rng(1)
    n = 20
    M = rng.uniform(0, 0.04, size=(n, n))
    np.fill_diagonal(M, 0.0)
    dy = np.zeros(n)
    dy[3] = 1_000_000.0

    out = leontief.propagate_demand_split(sp.csr_matrix(M), dy)

    np.testing.assert_allclose(out["initial"] + out["propagation"], out["total"], atol=1e-6)
    # propagation 만큼 더 큰 영향 — initial 보다 |total| 이 크다
    assert abs(out["total"][3]) >= abs(out["initial"][3])


def test_supply_split_two_directions() -> None:
    rng = np.random.default_rng(2)
    n = 15
    H = rng.uniform(0, 0.05, size=(n, n))
    np.fill_diagonal(H, 0.0)
    dcost = np.zeros(n)
    dcost[0] = 500_000.0
    drev = np.zeros(n)
    drev[0] = 800_000.0

    out = leontief.propagate_supply_split(sp.csr_matrix(H), dcost, drev)

    np.testing.assert_allclose(
        out["cost_initial"] + out["cost_propagation"], out["cost_total"], atol=1e-6
    )
    np.testing.assert_allclose(
        out["revenue_initial"] + out["revenue_propagation"], out["revenue_total"], atol=1e-6
    )
