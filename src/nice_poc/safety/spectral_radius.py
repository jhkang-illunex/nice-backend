"""ρ(A) 사전 체크 + row-normalize. 구현명세서 §5.1, 그래프모델 v2.1 §8.3.3."""
from __future__ import annotations

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import ArpackError, ArpackNoConvergence, eigs

DEFAULT_THRESHOLD = 0.95


def estimate(M: sp.spmatrix, *, max_iter: int = 500, tol: float = 1e-6) -> float:
    """Largest-magnitude eigenvalue of M (spectral radius).

    - N ≤ 2: ARPACK 의 k ≤ N-2 제약 때문에 dense ``numpy.linalg.eigvals`` 로 직접 계산.
    - N > 2: ARPACK ``eigs(k=1, which='LM')``.
    - Fallback: power iteration (ARPACK 수렴 실패 시).
    """
    n = M.shape[0]
    if n == 0:
        return 0.0
    if n <= 2:
        return float(np.max(np.abs(np.linalg.eigvals(M.toarray()))))
    try:
        vals = eigs(M.astype("float64"), k=1, which="LM", maxiter=max_iter, tol=tol,
                    return_eigenvectors=False)
        return float(np.abs(vals[0]))
    except (ArpackError, ArpackNoConvergence, ValueError, TypeError):
        return _power_iteration(M, max_iter=max_iter, tol=tol)


def _power_iteration(M: sp.spmatrix, *, max_iter: int, tol: float) -> float:
    n = M.shape[0]
    rng = np.random.default_rng(0)
    v = rng.standard_normal(n)
    v /= np.linalg.norm(v) or 1.0
    rho_prev = 0.0
    for _ in range(max_iter):
        w = M @ v
        rho = float(np.linalg.norm(w))
        if rho == 0.0:
            return 0.0
        v = w / rho
        if abs(rho - rho_prev) < tol * max(rho, 1.0):
            return rho
        rho_prev = rho
    return rho_prev


def is_safe(rho: float, threshold: float = DEFAULT_THRESHOLD) -> bool:
    return rho < threshold


def row_normalize(M: sp.spmatrix) -> sp.csr_matrix:
    """행합이 1을 초과하는 행만 1/row_sum 으로 스케일."""
    csr = M.tocsr()
    row_sum = np.asarray(np.abs(csr).sum(axis=1)).ravel()
    over = row_sum > 1.0
    if not over.any():
        return csr
    scale = np.ones_like(row_sum)
    scale[over] = 1.0 / row_sum[over]
    return (sp.diags(scale) @ csr).tocsr()


def check_and_normalize(
    M: sp.spmatrix, threshold: float = DEFAULT_THRESHOLD
) -> tuple[sp.csr_matrix, float, bool]:
    rho = estimate(M)
    if rho >= threshold:
        return row_normalize(M), rho, True
    return M.tocsr(), rho, False
