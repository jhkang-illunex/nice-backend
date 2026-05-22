"""ETL 순수 로직 단위 테스트 — DB 의존성 없음."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from nice_poc.etl.sinks.pg import _build_upsert_sql
from nice_poc.etl.sources.csv_source import CsvSource


def test_upsert_sql_shape() -> None:
    sql = _build_upsert_sql("firms", ["firm_id", "firm_name", "cri_score"], ["firm_id"])
    assert "INSERT INTO firms" in sql
    assert "ON CONFLICT (firm_id) DO UPDATE SET" in sql
    assert "firm_name = EXCLUDED.firm_name" in sql
    assert "cri_score = EXCLUDED.cri_score" in sql
    # PK 컬럼은 SET 절에 들어가지 않는다
    assert "firm_id = EXCLUDED.firm_id" not in sql


def test_upsert_sql_composite_pk() -> None:
    sql = _build_upsert_sql("impacts", ["run_id", "firm_id", "revenue_sum"], ["run_id", "firm_id"])
    assert "ON CONFLICT (run_id, firm_id)" in sql
    assert "revenue_sum = EXCLUDED.revenue_sum" in sql


def test_csv_source_parses_numeric_columns(tmp_path: Path) -> None:
    (tmp_path / "masters").mkdir()
    (tmp_path / "masters" / "sectors.csv").write_text(
        "code,name,level,parent_code,color\nC26,전자부품,2,C,#000\n", encoding="utf-8"
    )
    (tmp_path / "masters" / "hs_codes.csv").write_text(
        "code,name,hs2,hs4,elasticity\n854231,SSD,85,8542,-1.2\n", encoding="utf-8"
    )
    (tmp_path / "masters" / "countries.csv").write_text(
        "iso_alpha2,name_kr,name_en\nKR,한국,Korea\n", encoding="utf-8"
    )

    src = CsvSource(root=tmp_path)
    sectors = src.sectors()
    hs = src.hs_codes()

    assert sectors.loc[0, "level"] == 2
    assert hs.loc[0, "elasticity"] == -1.2
    # 한글 보존
    assert sectors.loc[0, "name"] == "전자부품"


def test_csv_source_missing_optional_numeric_columns(tmp_path: Path) -> None:
    # 명세서 폴백 로직: 일부 분모 컬럼이 비어 있어도 정상 처리
    (tmp_path / "firms.csv").write_text(
        "firm_id,biz_no,rep_bizno,firm_name,sector_code,base_year,sales_year_fin\n"
        "F1,1234567890123,1234567890123,X,C26,2024,1000000\n",
        encoding="utf-8",
    )
    df = CsvSource(root=tmp_path).firms()
    assert df.loc[0, "sales_year_fin"] == 1_000_000
    assert pd.isna(df.get("vat_fs_est_sales", pd.Series([None])).iloc[0])
