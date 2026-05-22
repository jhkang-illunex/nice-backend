"""원천 어댑터 Protocol.

도메인별로 ``DataFrame`` 을 yield 한다. 컬럼 계약은 docstring 참조.
구현체: :class:`~nice_poc.etl.sources.csv_source.CsvSource`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd


class FirmsSource(Protocol):
    def firms(self) -> pd.DataFrame:
        """columns: firm_id, biz_no, rep_bizno, firm_name, sector_code, base_year,
        sales_year_fin, sales_year_vat_observed, vat_fs_est_sales, vat_fs_est_purchase,
        inventory, value_added_year_fin, employees_count, cri_score, cri_year, watch_grade
        """
        ...


class SuppliesSource(Protocol):
    def supplies(self) -> pd.DataFrame:
        """columns: source_id, target_id, year, amount, observed_flag, obl_yn,
        number_observed_month, source_cate, target_cate,
        purchase_weight (옵션, 미존재시 후행 계산), sales_weight (옵션)
        """
        ...


class TradeSource(Protocol):
    def trade(self) -> pd.DataFrame:
        """columns: firm_id, hs6, iso_alpha2, year, direction (EXP/IMP),
        amount, weight_hs, weight_nation, rank
        """
        ...


class MastersSource(Protocol):
    def sectors(self) -> pd.DataFrame:    # code, name, level, parent_code, color
        ...
    def hs_codes(self) -> pd.DataFrame:   # code, name, hs2, hs4, elasticity
        ...
    def countries(self) -> pd.DataFrame:  # iso_alpha2, name_kr, name_en
        ...


__all__ = [
    "FirmsSource",
    "SuppliesSource",
    "TradeSource",
    "MastersSource",
    "Path",
]
