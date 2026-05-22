"""max-delta cap. 구현명세서 §5.2, 그래프모델 v2.1 §8.3.3 #3.

변화량 |Δ| 가 노드의 기준값(매출/매입) 의 100% 를 초과하면 sign × 한도로 cap.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_THRESHOLD = 1.0  # 100%


def cap_revenue(
    delta: pd.Series,
    base_sales: pd.Series,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[pd.Series, pd.Series]:
    return _cap(delta, base_sales, threshold=threshold)


def cap_cost(
    delta: pd.Series,
    base_purchase: pd.Series,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[pd.Series, pd.Series]:
    return _cap(delta, base_purchase, threshold=threshold)


def _cap(
    delta: pd.Series,
    base: pd.Series,
    *,
    threshold: float,
) -> tuple[pd.Series, pd.Series]:
    aligned_base = base.reindex(delta.index).astype("float64").fillna(0.0)
    limit = aligned_base.abs() * threshold
    over = delta.abs() > limit
    capped_values = np.where(over, np.sign(delta) * limit, delta)
    capped = pd.Series(capped_values, index=delta.index, dtype="float64")
    flag = pd.Series(over.to_numpy(), index=delta.index)
    return capped, flag


def capped_ratio(flag: pd.Series) -> float:
    if flag.empty:
        return 0.0
    return float(flag.sum()) / float(len(flag))
