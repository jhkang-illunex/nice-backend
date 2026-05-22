"""initial / propagation / total 분리. 구현명세서 §11.B7.

- Demand 후방 파급 : (I - H)^-1 @ Δy
- Supply 비용 후방 : (I - H)^-1 @ Δcost   (purchase_weight 동일)
- Supply 매출 전방 : (I - B^T)^-1 @ Δrev  (sales_weight 의 전치)

PoC 1차에서는 B 행렬 미구현 — Supply 매출 전방은 호출자가 B 를 제공하지 않으면
``H.T`` 로 폴백(보수적 근사). 정확한 B 는 5주차 ``matrix/matrix_B.py``.
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from nice_poc.propagation import bicgstab


def propagate_demand_split(
    H: sp.spmatrix,
    delta_y: np.ndarray,
    *,
    rtol: float = 1e-6,
    max_iter: int = 500,
) -> dict[str, np.ndarray]:
    initial = np.asarray(delta_y, dtype="float64").ravel()
    total = bicgstab.solve(H, initial, rtol=rtol, max_iter=max_iter)
    return {
        "initial": initial.copy(),
        "propagation": total - initial,
        "total": total,
    }


def propagate_supply_split(
    H: sp.spmatrix,
    delta_cost: np.ndarray,
    delta_revenue: np.ndarray,
    *,
    B: sp.spmatrix | None = None,
    rtol: float = 1e-6,
    max_iter: int = 500,
) -> dict[str, np.ndarray]:
    dcost = np.asarray(delta_cost, dtype="float64").ravel()
    drev = np.asarray(delta_revenue, dtype="float64").ravel()

    cost_total = bicgstab.solve(H, dcost, rtol=rtol, max_iter=max_iter)

    forward = (B if B is not None else H).T
    rev_total = bicgstab.solve(forward, drev, rtol=rtol, max_iter=max_iter)

    return {
        "cost_initial": dcost.copy(),
        "cost_propagation": cost_total - dcost,
        "cost_total": cost_total,
        "revenue_initial": drev.copy(),
        "revenue_propagation": rev_total - drev,
        "revenue_total": rev_total,
    }
