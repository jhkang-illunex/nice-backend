"""문장형 질의 품목 추출 — LLM 비의존 부분(게이트·환각 필터) 단위 테스트."""

import pytest

from nice_rag.search.extract import filter_items, looks_like_sentence
from nice_rag.search.normalize import normalize_query


# ─── looks_like_sentence — 키워드형은 LLM 미호출이 보장돼야 함 ───────────────


@pytest.mark.parametrize(
    "query",
    [
        "립스틱",
        "듀럼밀",
        "열연강판",
        "스테인리스 냉연강판",
        "반도체용 감광액 포토레지스트",  # 3토큰이지만 노이즈 어휘 없음
        "전기차 배터리",
    ],
)
def test_keyword_queries_skip_llm(query):
    assert not looks_like_sentence(normalize_query(query))


@pytest.mark.parametrize(
    "query",
    [
        "경주마를 수입할 때 어떤 HS 코드를 사용하나요?",
        "전기자동차 관세율은 어떻게 되나요",
        "프랑스에서 제빵용 밀가루를 수입하려고 합니다 부호 알려주세요",
        "반도체용 포토레지스트 관세 얼마인가요",
        "리튬이온 배터리셀의 HS 부호가 궁금합니다",
        "살아 있는 소를 농장에서 기르기 위해 들여오고 싶은데 분류 기준이 뭔가요?",  # ?
    ],
)
def test_sentence_queries_trigger_extraction(query):
    assert looks_like_sentence(normalize_query(query))


# ─── filter_items — 원 질의에 없는 항목(환각)은 버려져야 함 ──────────────────


def test_filter_keeps_substring_items():
    q = normalize_query("경주마를 수입할 때 어떤 HS 코드를 사용하나요?")
    assert filter_items(["경주마"], q) == ["경주마"]


def test_filter_drops_hallucinated_items():
    q = normalize_query("경주마를 수입할 때 어떤 HS 코드를 사용하나요?")
    assert filter_items(["승용마", "말고기"], q) == []


def test_filter_allows_spacing_variation():
    q = normalize_query("리튬이온배터리 관세율이 얼마인가요")
    assert filter_items(["리튬이온 배터리"], q) == ["리튬이온 배터리"]


def test_filter_dedupes_and_caps_at_three():
    q = normalize_query("밀가루 버터 설탕 소금 들여오려고 하는데 관세가 궁금해요")
    items = ["밀가루", "밀가루", "버터", "설탕", "소금"]
    assert filter_items(items, q) == ["밀가루", "버터", "설탕"]


def test_filter_drops_short_or_empty():
    q = normalize_query("말 수입 관세")
    assert filter_items(["말", ""], q) == []
