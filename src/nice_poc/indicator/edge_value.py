"""Edge Value. 구현명세서 §6.1.

Edge_ij = pass_through × purchase_weight_ij = s × (z_ij / 매입_j)
"""

from __future__ import annotations

import pandas as pd


def compute(supplies: pd.DataFrame, *, pass_through: float = 1.0) -> pd.DataFrame:
    """``supplies`` 에 'edge_value' 컬럼을 더해 반환.

    필요한 컬럼: source_id, target_id, year, purchase_weight.
    """
    out = supplies.copy()
    out["edge_value"] = pass_through * out["purchase_weight"].astype("float64")
    return out
