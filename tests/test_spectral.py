"""구현명세서 §9.1 test_spectral.py — ρ 추정 정확도, row_normalize 후 ρ < 1."""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from nice_poc.safety import spectral_radius as sr


def test_estimate_diagonal() -> None:
    M = sp.diags([0.3, 0.7, 0.5])
    rho = sr.estimate(M)
    np.testing.assert_allclose(rho, 0.7, atol=1e-4)


def test_estimate_random_dense_vs_numpy() -> None:
    rng = np.random.default_rng(42)
    A = rng.uniform(0, 0.1, size=(20, 20))
    expected = float(np.max(np.abs(np.linalg.eigvals(A))))
    rho = sr.estimate(sp.csr_matrix(A))
    np.testing.assert_allclose(rho, expected, rtol=1e-3)


def test_is_safe_threshold() -> None:
    assert sr.is_safe(0.94)
    assert not sr.is_safe(0.95)
    assert not sr.is_safe(1.2)


def test_row_normalize_caps_row_sum() -> None:
    M = sp.csr_matrix(np.array([
        [0.4, 0.4, 0.4],   # sum = 1.2 → normalize
        [0.2, 0.3, 0.0],   # sum = 0.5 → keep
        [0.0, 0.0, 0.0],
    ]))
    N = sr.row_normalize(M)
    row_sum = np.asarray(np.abs(N).sum(axis=1)).ravel()
    assert (row_sum <= 1.0 + 1e-12).all()
    np.testing.assert_allclose(row_sum[1], 0.5)


def test_check_and_normalize_only_when_unsafe() -> None:
    safe = sp.diags([0.3, 0.4])
    out, rho, normalized = sr.check_and_normalize(safe)
    assert not normalized
    np.testing.assert_allclose(rho, 0.4, atol=1e-4)

    unsafe = sp.csr_matrix(np.array([[0.0, 0.95], [0.95, 0.0]]))
    out, rho, normalized = sr.check_and_normalize(unsafe)
    assert normalized
    assert rho >= 0.95
    # 정규화 후 ρ 다시 측정
    rho_after = sr.estimate(out)
    assert rho_after < 1.0
