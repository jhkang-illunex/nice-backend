"""BiCGSTAB 풀이 + GMRES 폴백. 구현명세서 §4.2.

수식: (I - M) x = b 를 풀어 x 반환.
- M : sparse coefficient matrix (Demand: H, Supply 비용 후방: H, Supply 매출 전방: B.T)
- b : 1차 충격 벡터 (Δy 또는 Δcost)
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import bicgstab as _bicgstab
from scipy.sparse.linalg import gmres as _gmres


class SolverError(RuntimeError):
    pass


def solve(
    M: sp.spmatrix,
    b: np.ndarray,
    *,
    rtol: float = 1e-6,
    max_iter: int = 500,
) -> np.ndarray:
    n = M.shape[0]
    A = (sp.eye(n, format="csr") - M.tocsr()).tocsr()
    b = np.asarray(b, dtype="float64").ravel()

    x, info = _bicgstab(A, b, rtol=rtol, maxiter=max_iter)
    if info == 0:
        return x

    x, info = _gmres(A, b, rtol=rtol, maxiter=max_iter, restart=50)
    if info == 0:
        return x

    raise SolverError(f"both BiCGSTAB and GMRES failed (info={info})")
