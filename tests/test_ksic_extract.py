"""KSIC 업종 표현 추출 단위 테스트 — LLM 은 monkeypatch (실호출 없음)."""

from __future__ import annotations

import pytest

from nice_rag.search import ksic_extract
from nice_rag.search.ksic_extract import (
    extract_industry,
    looks_like_industry_sentence,
)


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        # 키워드형 — LLM 게이트 통과 안 함
        ("반도체", False),
        ("화물 운송", False),
        ("소프트웨어 개발", False),
        # 물음표 즉시 문장형
        ("반도체 만드는 회사는 어떤 산업분류에 속하나요?", True),
        # 6 토큰 이상
        ("서울에서 커피 원두를 볶아 도매로 납품하는 회사", True),
        # 3 토큰 + 업종 노이즈 어휘
        ("음식점 업종코드 알려줘", True),
    ],
)
def test_gate(q: str, expected: bool) -> None:
    assert looks_like_industry_sentence(q) is expected


def test_extract_keyword_query_skips_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(query: str) -> list[str]:
        raise AssertionError("키워드형 질의는 LLM 을 호출하면 안 됨")

    monkeypatch.setattr(ksic_extract, "_llm_items", _boom)
    assert extract_industry("반도체") is None


def test_extract_filters_hallucination(monkeypatch: pytest.MonkeyPatch) -> None:
    # '제조'는 원 질의에 없음 → 차단, '반도체'만 통과
    monkeypatch.setattr(ksic_extract, "_llm_items", lambda q: ["반도체", "제조"])
    out = extract_industry("반도체 만드는 회사는 어떤 산업분류에 속하나요?")
    assert out == "반도체"


def test_extract_multi_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ksic_extract, "_llm_items", lambda q: ["소프트웨어 개발", "데이터베이스 구축"]
    )
    out = extract_industry("소프트웨어 개발과 데이터베이스 구축을 하는 스타트업입니다")
    assert out == "소프트웨어 개발 데이터베이스 구축"


def test_extract_llm_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ksic_extract, "_llm_items", lambda q: [])
    assert extract_industry("반도체 만드는 회사는 어떤 산업분류에 속하나요?") is None


def test_extract_disabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from nice_rag.config import get_rag_settings

    s = get_rag_settings()
    monkeypatch.setattr(s, "ksic_extract_enabled", False)
    monkeypatch.setattr(
        ksic_extract, "_llm_items",
        lambda q: pytest.fail("비활성화 시 LLM 호출 금지"),
    )
    assert extract_industry("반도체 만드는 회사는 어떤 산업분류에 속하나요?") is None
