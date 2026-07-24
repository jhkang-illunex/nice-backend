"""thinking 모델(qwen3 등) <think> 출력 대응 테스트.

nice_llm.client.strip_reasoning 이 <think>...</think> 를 제거해
(1) chat_json JSON 파싱이 깨지지 않고 (2) /agent 답변에 추론이 누출되지 않음을 보장.
"""
from nice_llm.client import parse_json_lenient, strip_reasoning


def test_strip_removes_think_block_with_braces():
    # think 안에 스키마 중괄호가 섞인 흔한 케이스 — 제거 후 실제 JSON 만 남아야
    raw = '<think>스키마는 {"category":"..."} 형태. 답은 RELATED.</think>\n{"category":"RELATED","reason":"밸브"}'
    cleaned = strip_reasoning(raw)
    assert "<think>" not in cleaned
    assert parse_json_lenient(cleaned) == {"category": "RELATED", "reason": "밸브"}


def test_strip_agent_answer_no_leak():
    raw = "<think>후보 중 8481 이 맞다. 이유는...</think>\n밸브류는 HS 8481 에 해당합니다."
    assert strip_reasoning(raw) == "밸브류는 HS 8481 에 해당합니다."


def test_strip_empty_think_tags():
    # no_think 모드에서 빈 태그를 남기는 변형
    assert strip_reasoning("<think>\n\n</think>\n답변") == "답변"


def test_strip_closing_only_variant():
    # 여는 태그 없이 </think> 로만 끝나는 백엔드 변형
    assert strip_reasoning("추론 텍스트</think>\n실제 답변") == "실제 답변"


def test_non_think_model_is_noop():
    # 비-think 모델: 태그 없음 → 원문 그대로(양끝 공백만 정리)
    assert strip_reasoning("HS 8481 밸브") == "HS 8481 밸브"
    assert strip_reasoning('{"category":"RELATED"}') == '{"category":"RELATED"}'


def test_multiline_reasoning_removed():
    raw = "<think>\n줄1\n줄2\n{잡음}\n</think>\n{\"k\":\"v\"}"
    assert parse_json_lenient(strip_reasoning(raw)) == {"k": "v"}
