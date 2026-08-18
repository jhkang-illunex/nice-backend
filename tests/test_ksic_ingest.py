"""ksic 적재 파이프라인 순수 로직 단위 테스트 — DB 의존성 없음."""

from __future__ import annotations

import pytest

from nice_ingest.pipelines.ksic.pipeline import (
    build_search_text,
    parse_rows,
    split_section_name,
)

# 실제 파일의 wide 포맷 축약 재현 — 제목 2행 + '코드' 헤더 행 + 데이터
_SAMPLE_ROWS = [
    ("개정 분류체계(제11차 기준)", None, None, None, None, None, None, None, None, None),
    ("대분류(21)", None, "중분류(77)", None, "소분류(234)", None, "세분류(501)", None, "세세분류(1,205)", None),
    ("코드", "항목명", "코드", "항목명", "코드", "항목명", "코드", "항목명", "코드", "항목명"),
    ("A", "농업, 임업 및 어업(01~03)", "01", "농업", "011", "작물 재배업", "0111", "곡물 및 기타 식량작물 재배업", "01110", "곡물 및 기타 식량작물 재배업"),
    (None, None, None, None, None, None, "0112", "채소, 화훼작물 및 종묘 재배업", "01121", "채소작물 재배업"),
    (None, None, "02", "임업", "020", "임업", "0201", "영림업", "02011", "육림업"),
    ("U", "국제 및 외국기관(99)", "99", "국제 및 외국기관", "990", "국제 및 외국기관", "9900", "국제 및 외국기관", "99001", "주한 외국공관"),
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("농업, 임업 및 어업(01~03)", ("농업, 임업 및 어업", "01~03")),
        ("전기, 가스, 증기 및 공기 조절 공급업(35)", ("전기, 가스, 증기 및 공기 조절 공급업", "35")),
        ("국제 및 외국기관(99)", ("국제 및 외국기관", "99")),
        # 범위 표기가 없으면 원문 유지 + None
        ("범위 없는 이름", ("범위 없는 이름", None)),
    ],
)
def test_split_section_name(raw: str, expected: tuple[str, str | None]) -> None:
    assert split_section_name(raw) == expected


def test_parse_rows_levels_and_parents() -> None:
    records, counts = parse_rows(_SAMPLE_ROWS)
    assert counts == {1: 2, 2: 3, 3: 3, 4: 4, 5: 4}

    by_code = {r["code"]: r for r in records}
    assert by_code["A"]["level"] == 1
    assert by_code["A"]["parent_code"] is None
    assert by_code["A"]["division_range"] == "01~03"
    assert by_code["01"]["level"] == 2
    assert by_code["01"]["parent_code"] == "A"
    assert by_code["99"]["parent_code"] == "U"

    # FK 만족을 위해 대분류가 중분류보다 먼저
    codes = [r["code"] for r in records]
    assert codes.index("A") < codes.index("01")
    assert codes.index("U") < codes.index("99")


def test_parse_rows_children_absorption() -> None:
    """소분류 이하 항목명이 상위 검색 텍스트에 흡수되는지 — 리콜의 핵심 경로."""
    records, _ = parse_rows(_SAMPLE_ROWS)
    by_code = {r["code"]: r for r in records}

    # 중분류 01: 소·세·세세분류 명칭 포함 (동명 중복은 1회만)
    div01 = by_code["01"]
    assert "작물 재배업" in div01["children_text"]
    assert "채소작물 재배업" in div01["children_text"]
    assert div01["children_text"].count("곡물 및 기타 식량작물 재배업") == 1
    # 다른 중분류(02) 하위는 섞이지 않음
    assert "영림업" not in div01["children_text"]
    assert "영림업" in by_code["02"]["children_text"]

    # 대분류 A: 중분류명 + 소분류명까지만 (세분류 이하 제외 — 임베딩 입력 비대 방지)
    sec_a = by_code["A"]
    assert "농업" in sec_a["children_text"]
    assert "작물 재배업" in sec_a["children_text"]
    assert "채소작물 재배업" not in sec_a["children_text"]

    # search_text 포맷: name | 상위명 | 하위명들
    assert div01["search_text"].startswith("농업 | 농업, 임업 및 어업 | ")


def test_build_search_text_dedup() -> None:
    out = build_search_text("농업", "농업, 임업 및 어업", ["작물 재배업", "작물 재배업", "영림업"])
    assert out == "농업 | 농업, 임업 및 어업 | 작물 재배업 영림업"


def test_parse_rows_division_before_section_raises() -> None:
    rows = [
        ("코드", "항목명", "코드", "항목명", "코드", "항목명", "코드", "항목명", "코드", "항목명"),
        (None, None, "01", "농업", None, None, None, None, None, None),
    ]
    with pytest.raises(ValueError, match="대분류보다 먼저"):
        parse_rows(rows)
