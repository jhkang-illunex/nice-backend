"""H 행렬 산출. 구현명세서 §2.2.

H_ij = (i 가 j 에게 공급한 중간재 금액) / (j 의 총매출)

- 분자: 자본재 / B2C / GOV 거래 제외, 연도 필터 후 (source_id, target_id) 합산
- 분모 우선순위: sales_year_fin → vat_fs_est_sales → ml_estimate_sales (§2.2.2)
- 분자합 > 분모 보정: 열합 col_sum > 1 이면 해당 열 정규화 (§2.2.3)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import scipy.sparse as sp

DENOMINATOR_PRIORITY: tuple[str, ...] = (
    "sales_year_fin",
    "vat_fs_est_sales",
    "ml_estimate_sales",
)


@dataclass(frozen=True)
class HMatrix:
    H: sp.csr_matrix
    firm_ids: pd.Index
    denominators: pd.Series  # firm_id → 최종 분모


def _resolve_denominator(firms: pd.DataFrame) -> pd.Series:
    out = pd.Series(np.nan, index=firms.index, dtype="float64")
    for col in DENOMINATOR_PRIORITY:
        if col not in firms.columns:
            continue
        mask = out.isna() & firms[col].notna() & (firms[col] > 0)
        out.loc[mask] = firms.loc[mask, col].astype("float64")
    return out


def build(
    edges: pd.DataFrame,
    firms: pd.DataFrame,
    year: int,
    capital_codes: set[str] | None = None,
) -> HMatrix:
    """Build the H matrix for a single year.

    Parameters
    ----------
    edges : columns {source_id, target_id, year, amount, target_cate}
            optional: capital_code
    firms : DataFrame indexed by firm_id, with denominator columns
    year  : target year (멀티엣지 키)
    """
    e = edges[edges["year"] == year]
    e = e[~e["target_cate"].isin(("B2C", "GOV"))]
    if capital_codes and "capital_code" in e.columns:
        e = e[~e["capital_code"].isin(capital_codes)]

    firm_ids = firms.index
    denom = _resolve_denominator(firms)
    n = len(firm_ids)

    if e.empty:
        return HMatrix(H=sp.csr_matrix((n, n)), firm_ids=firm_ids, denominators=denom)

    z = e.groupby(["source_id", "target_id"], as_index=False)["amount"].sum()

    idx_of = pd.Series(np.arange(n), index=firm_ids)
    z = z[z["source_id"].isin(idx_of.index) & z["target_id"].isin(idx_of.index)]

    row = idx_of.loc[z["source_id"]].to_numpy()
    col = idx_of.loc[z["target_id"]].to_numpy()
    data = z["amount"].to_numpy(dtype="float64")

    col_denom = denom.reindex(firm_ids).to_numpy()
    valid = col_denom[col] > 0
    row, col, data = row[valid], col[valid], data[valid]
    data = data / col_denom[col]

    H = sp.coo_matrix((data, (row, col)), shape=(n, n)).tocsr()

    col_sum = np.asarray(H.sum(axis=0)).ravel()
    over = col_sum > 1.0
    if over.any():
        scale = np.ones_like(col_sum)
        scale[over] = 1.0 / col_sum[over]
        H = (H @ sp.diags(scale)).tocsr()

    return HMatrix(H=H, firm_ids=firm_ids, denominators=denom)
