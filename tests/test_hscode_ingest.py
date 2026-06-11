"""hscode 적재 파이프라인 순수 로직 단위 테스트 — DB 의존성 없음."""

from __future__ import annotations

import pytest

from nice_ingest.pipelines.hscode.pipeline import _normalize_hs_code, transform_row


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 10자리 텍스트 셀 — 그대로
        ("0101211000", "0101211000"),
        # 텍스트 셀은 선행 0 보존 + 후행 0 생략 — 오른쪽 패딩만 (절대 선행 0 추가 금지)
        ("01059910", "0105991000"),  # 8자리 (01류)
        ("271020974", "2710209740"),  # 9자리 비0 시작 (방청유 2710류) — 선행 0 붙이면 유령 코드
        ("0507901", "0507901000"),  # 7자리 (05류)
        ("180690219", "1806902190"),  # 9자리 (18류 초콜릿)
        # 숫자 셀 — 선행 0 소실이므로 홀수 길이만 1개 복원 후 오른쪽 패딩
        (101211000, "0101211000"),
        (2071320, "0207132000"),
        # 거부 케이스
        ("12345678901", None),  # 10자리 초과
        ("abc", None),  # 비숫자
        (None, None),
        ("", None),
    ],
)
def test_normalize_hs_code(raw: object, expected: str | None) -> None:
    assert _normalize_hs_code(raw) == expected


def test_normalize_never_produces_chapter_00() -> None:
    """과거 zfill(10) 버그 회귀 방지 — 어떤 입력도 00류를 만들면 안 된다."""
    for raw in ("01059910", 2071320, "0507901", 101211000, "0101211000"):
        out = _normalize_hs_code(raw)
        assert out is not None
        assert not out.startswith("00"), f"{raw!r} -> {out}"


def test_transform_row_rejects_bad_code() -> None:
    row = transform_row({"hs_code": "abc", "valid_from": "2024-01-01", "valid_to": "2026-12-31"})
    assert row is None


def test_expand_query_known_aliases() -> None:
    from nice_rag.search.synonyms import expand_query

    assert "축전지" in expand_query("리튬이온전지 수입")
    assert "전자석" in expand_query("네오디뮴 영구자석")
    assert "밀" in expand_query("듀럼밀")
    # 매칭 없는 질의는 정규화만 적용
    assert expand_query("천연가스") == "천연가스"
    # 정규화 동작 포함 (괄호 제거)
    assert expand_query("화장품(립스틱)").startswith("화장품 립스틱")
