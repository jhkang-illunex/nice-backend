"""upload.py 단위 테스트 — DB 의존성 없는 부분."""

from __future__ import annotations

from pathlib import Path

import pytest

from nice_poc.etl.upload import _read_csv


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_read_csv_with_rename(tmp_path: Path) -> None:
    csv = tmp_path / "in.csv"
    _write(csv, "기업ID,기업명\nF001,에이전자\nF002,비반도체\n")

    df = _read_csv(
        csv,
        rename={"기업ID": "firm_id", "기업명": "firm_name"},
        columns=None,
        delimiter=",",
        encoding="utf-8",
    )

    assert list(df.columns) == ["firm_id", "firm_name"]
    assert df.loc[0, "firm_id"] == "F001"
    assert df.loc[1, "firm_name"] == "비반도체"


def test_read_csv_required_columns_missing(tmp_path: Path) -> None:
    csv = tmp_path / "in.csv"
    _write(csv, "a,b\n1,2\n")
    with pytest.raises(KeyError, match="missing required columns"):
        _read_csv(csv, rename=None, columns=["a", "c"], delimiter=",", encoding="utf-8")


def test_read_csv_columns_filter_and_reorder(tmp_path: Path) -> None:
    csv = tmp_path / "in.csv"
    _write(csv, "b,a,c\n2,1,3\n")
    df = _read_csv(csv, rename=None, columns=["a", "b"], delimiter=",", encoding="utf-8")
    assert list(df.columns) == ["a", "b"]
    assert df.loc[0, "a"] == 1
    assert "c" not in df.columns


def test_dry_run_does_not_call_sink(tmp_path: Path) -> None:
    from nice_poc.etl.upload import upload_to_pg

    csv = tmp_path / "in.csv"
    _write(csv, "firm_id,firm_name\nF001,X\n")

    class _BoomSink:
        def upsert(self, *a: object, **k: object) -> int:
            raise AssertionError("sink should not be called in dry-run")

    report = upload_to_pg(
        csv,
        table="firms",
        pk=["firm_id"],
        dry_run=True,
        sink=_BoomSink(),  # type: ignore[arg-type]
    )
    assert report.dry_run is True
    assert report.rows_read == 1
    assert report.rows_loaded == 0


def test_parse_rename_cli_helper() -> None:
    from nice_poc.etl.__main__ import _parse_rename

    assert _parse_rename(None) == {}
    assert _parse_rename("") == {}
    assert _parse_rename("a=b,c=d") == {"a": "b", "c": "d"}
    assert _parse_rename(" 기업ID = firm_id ") == {"기업ID": "firm_id"}


def test_split_csv_helper() -> None:
    from nice_poc.etl.__main__ import _split_csv

    assert _split_csv(None) is None
    assert _split_csv("a,b ,c") == ["a", "b", "c"]
