"""CSV 디렉토리 기반 원천 어댑터.

레이아웃::

    <root>/
    ├── firms.csv
    ├── supplies.csv
    ├── trade.csv
    └── masters/
        ├── sectors.csv
        ├── hs_codes.csv
        └── countries.csv
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True, slots=True)
class CsvSource:
    root: Path

    def _read(self, rel: str) -> pd.DataFrame:
        return pd.read_csv(self.root / rel, dtype=str, keep_default_na=True, na_values=[""])

    def firms(self) -> pd.DataFrame:
        df = self._read("firms.csv")
        numeric = [
            "base_year",
            "sales_year_fin",
            "sales_year_vat_observed",
            "vat_fs_est_sales",
            "vat_fs_est_purchase",
            "inventory",
            "value_added_year_fin",
            "employees_count",
            "cri_score",
            "cri_year",
        ]
        for c in numeric:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def supplies(self) -> pd.DataFrame:
        df = self._read("supplies.csv")
        for c in ("year", "amount", "number_observed_month", "purchase_weight", "sales_weight"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def trade(self) -> pd.DataFrame:
        df = self._read("trade.csv")
        for c in ("year", "amount", "weight_hs", "weight_nation", "rank"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def sectors(self) -> pd.DataFrame:
        df = self._read("masters/sectors.csv")
        if "level" in df.columns:
            df["level"] = pd.to_numeric(df["level"], errors="coerce").astype("Int64")
        return df

    def hs_codes(self) -> pd.DataFrame:
        df = self._read("masters/hs_codes.csv")
        if "elasticity" in df.columns:
            df["elasticity"] = pd.to_numeric(df["elasticity"], errors="coerce")
        return df

    def countries(self) -> pd.DataFrame:
        return self._read("masters/countries.csv")
