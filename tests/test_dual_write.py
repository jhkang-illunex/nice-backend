"""dual_write 헬퍼 단위 테스트 — DB 의존성 없는 부분."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from nice_poc.result.dual_write import (
    IMPACTS_PG_COLUMNS,
    SIMULATION_RUN_COLUMNS,
    _impacts_rows,
    _runs_row,
)
from nice_poc.result.impact_record import IMPACT_COLUMNS


def _table() -> pd.DataFrame:
    fid = pd.Index(["F001", "F002", "F003"], name="firm_id")
    return pd.DataFrame(
        {
            "revenue_initial": [100.0, 0.0, 0.0],
            "revenue_propagation": [0.0, 30.0, 5.0],
            "revenue_sum": [100.0, 30.0, 5.0],
            "cost_initial": [0.0, 0.0, 0.0],
            "cost_propagation": [0.0, 0.0, 0.0],
            "cost_sum": [0.0, 0.0, 0.0],
            "profit_initial": [100.0, 0.0, 0.0],
            "profit_propagation": [0.0, 30.0, 5.0],
            "profit_sum": [100.0, 30.0, 5.0],
        },
        index=fid,
    )


def test_runs_row_has_all_pg_columns() -> None:
    now = datetime.now(UTC)
    df = _runs_row(
        run_id="R1",
        scenario_id="S1",
        scenario_group_id=None,
        target_year=2024,
        iter_count=3,
        max_iter=8,
        epsilon=1_000_000.0,
        rho_a=0.42,
        rho_b=None,
        capped_ratio=0.05,
        executed_at=now,
        completed_at=now,
    )
    assert set(SIMULATION_RUN_COLUMNS).issubset(df.columns)
    assert df.loc[0, "status"] == "COMPLETED"
    assert df.loc[0, "iter"] == 3
    assert df.loc[0, "spectral_radius_a"] == 0.42
    assert df.loc[0, "spectral_radius_b"] is None


def test_impacts_rows_columns_and_fk_keys() -> None:
    impact_score = pd.Series([0.5, 0.1, 0.0], index=["F001", "F002", "F003"])
    capped = pd.Series([True, False, False], index=["F001", "F002", "F003"])

    df = _impacts_rows(
        _table(),
        run_id="R1",
        scenario_id="S1",
        scenario_group_id="G1",
        impact_score=impact_score,
        capped_flag=capped,
    )

    assert set(IMPACTS_PG_COLUMNS).issubset(df.columns)
    assert (df["run_id"] == "R1").all()
    assert (df["scenario_id"] == "S1").all()
    assert (df["scenario_group_id"] == "G1").all()
    assert list(df["firm_id"]) == ["F001", "F002", "F003"]
    # 9 컬럼 정확히 보존
    for col in IMPACT_COLUMNS:
        assert col in df.columns
    np.testing.assert_allclose(df["impact_score"].to_numpy(), [0.5, 0.1, 0.0])
    assert df["capped"].tolist() == [True, False, False]
    # ui_severity 는 PoC 1차 미정 — None 보존
    assert df["ui_severity"].isna().all()


def test_impacts_rows_defaults_when_score_and_flag_missing() -> None:
    df = _impacts_rows(
        _table(),
        run_id="R1",
        scenario_id=None,
        scenario_group_id=None,
        impact_score=None,
        capped_flag=None,
    )
    assert (df["impact_score"] == 0.0).all()
    assert (df["capped"] == False).all()  # noqa: E712 — pandas elementwise
    assert df["scenario_id"].isna().all()
