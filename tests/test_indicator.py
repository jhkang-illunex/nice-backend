"""edge_value + TIS 검증."""

from __future__ import annotations

import numpy as np
import pandas as pd

from nice_poc.indicator import edge_value, tis


def test_edge_value_pass_through_proportional() -> None:
    sup = pd.DataFrame(
        {
            "source_id": ["A", "A", "B"],
            "target_id": ["B", "C", "C"],
            "year": [2024, 2024, 2024],
            "purchase_weight": [0.1, 0.2, 0.4],
        }
    )
    out = edge_value.compute(sup, pass_through=0.5)
    np.testing.assert_allclose(out["edge_value"], [0.05, 0.10, 0.20])


def test_tis_components_and_score() -> None:
    idx = pd.Index(["A", "B", "C"], name="firm_id")
    delta_x = pd.Series([-200_000, -1_000_000, 0], index=idx, dtype="float64")
    firms = pd.DataFrame(
        {"sales_year_fin": [1_000_000, 5_000_000, 2_000_000], "cri_score": [3.0, 8.0, 5.0]},
        index=idx,
    )

    out = tis.compute(delta_x, firms)

    np.testing.assert_allclose(out["exposure"], [0.2, 0.2, 0.0])
    np.testing.assert_allclose(out["risk"], [0.3, 0.8, 0.5])
    np.testing.assert_allclose(out["tis"], [0.06, 0.16, 0.0])


def test_tis_zero_sales_yields_zero_exposure() -> None:
    delta_x = pd.Series([100.0], index=["A"])
    firms = pd.DataFrame({"sales_year_fin": [0], "cri_score": [5.0]}, index=["A"])
    out = tis.compute(delta_x, firms)
    assert out.loc["A", "exposure"] == 0.0
    assert out.loc["A", "tis"] == 0.0
