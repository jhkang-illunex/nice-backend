"""LLM JSON 응답 분류 wrapper (use-case helper).

chat completions 코어 클라이언트는 ``nice_llm`` 으로 이전. 이 모듈은
``nice_demo`` 데모의 "기업 컨텍스트 → {category, reason}" 분류 use-case 만
얇게 감싼 wrapper. 신규 코드는 ``nice_llm.LlmJsonClient`` /
``nice_llm.get_llm_json_client`` 를 직접 사용 권장.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from nice_llm import LlmJsonClient as _CoreLlmJsonClient
from nice_llm import get_llm_json_client as _get_core_client

_VALID_CATEGORIES: tuple[str, ...] = ("HIGH", "MEDIUM", "LOW", "NONE")


class DemoCompanyClassifier:
    """기업 메타 → {category in {HIGH,MEDIUM,LOW,NONE}, reason}."""

    def __init__(self, client: _CoreLlmJsonClient) -> None:
        self._client = client

    def classify_company(
        self,
        *,
        company_context: str,
        category_definitions: str,
    ) -> dict[str, Any]:
        """분류 결과 dict. 파싱 실패는 NONE 으로 fallback."""
        system = (
            "당신은 한국 무역 공급망 분석가입니다. "
            f"카테고리 정의: {category_definitions}"
        )
        result = self._client.classify_choice(
            system=system,
            user=company_context,
            choices=list(_VALID_CATEGORIES),
            field="category",
            extra_keys=("reason",),
        )
        if result is None:
            return {"category": "NONE", "reason": "(parse_failed)"}
        return result


@lru_cache
def get_llm_json_client() -> DemoCompanyClassifier:
    """nice_demo use-case classifier — nice_llm core 를 wrap."""
    return DemoCompanyClassifier(client=_get_core_client())


# 호환성: 기존 코드가 ``from nice_demo.clients import LlmJsonClient`` 로 받음.
LlmJsonClient = DemoCompanyClassifier
