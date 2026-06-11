"""DEPRECATED 모듈 위치 — chat completions 는 ``nice_llm`` 으로 이전.

기존 ``from nice_rag.clients import get_llm_client`` import 호환을 위해
``nice_llm`` 의 정의를 그대로 re-export 한다. 신규 코드는 ``nice_llm`` 을
직접 import 권장.
"""

from nice_llm import LlmClient, get_llm_client

__all__ = ["LlmClient", "get_llm_client"]
