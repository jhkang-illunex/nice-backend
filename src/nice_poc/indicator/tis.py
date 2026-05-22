"""Tariff Impact Score. 구현명세서 §6.2.

TIS_i      = Exposure_i × Risk_i
Exposure_i = |Δx_i| / sales_i
Risk_i     = cri_score_i / 10            (1~10 정규화)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CRI_SCALE = 10.0


def compute(
    delta_x: pd.Series,
    firms: pd.DataFrame,
    *,
    sales_col: str = "sales_year_fin",
    cri_col: str = "cri_score",
) -> pd.DataFrame:
    """반환: DataFrame[exposure, risk, tis] indexed by firm_id."""
    aligned = firms.reindex(delta_x.index)
    sales = aligned[sales_col].astype("float64").replace(0, np.nan)
    cri = aligned[cri_col].astype("float64")

    exposure = (delta_x.abs() / sales).fillna(0.0)
    risk = (cri / CRI_SCALE).fillna(0.0)
    score = exposure * risk

    return pd.DataFrame(
        {"exposure": exposure, "risk": risk, "tis": score},
        index=delta_x.index,
    )
