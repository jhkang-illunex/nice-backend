"""OpenAI-호환 chat completions 클라이언트 — 일반 + JSON 강제.

``LlmClient`` — raw chat. ``extra`` dict 로 임의 OpenAI 호환 키 주입 가능
(예: ``response_format``, ``tools``, ``seed``).

``LlmJsonClient`` — JSON object 강제. 응답을 dict 로 파싱해 돌려주고, 카테고리
분류 helper ``classify_choice`` 를 제공해 분류기 use-case 를 한 줄로 끝낼 수
있게 함. 파싱 실패는 빈 dict / None 으로 swallow — 호출자가 fallback 처리.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from nice_llm.settings import get_llm_settings

log = logging.getLogger(__name__)


# ─── 일반 chat ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LlmClient:
    base_url: str
    model: str
    api_key: str
    timeout_s: float
    reasoning_effort: str = ""

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """chat completions 호출 → assistant 메시지 content 반환.

        ``extra`` 로 OpenAI 호환 임의 키 (``response_format``, ``tools``,
        ``top_p``, ``seed`` 등) 를 body 에 그대로 주입.
        """
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if self.reasoning_effort:
            # qwen3 류 thinking 제어 — 'none' 이면 추론 토큰 없이 content 만.
            # (ollama 는 'think' 불리언/'/no_think' 소프트 스위치를 OpenAI-호환
            # 경로에서 무시하므로 reasoning_effort 가 유일한 제어 수단)
            body["reasoning_effort"] = self.reasoning_effort
        if extra:
            body.update(extra)

        with httpx.Client(timeout=self.timeout_s) as cx:
            r = cx.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"]["content"]


@lru_cache
def get_llm_client() -> LlmClient:
    s = get_llm_settings()
    return LlmClient(
        base_url=s.base_url,
        model=s.model,
        api_key=s.api_key,
        timeout_s=s.timeout_s,
        reasoning_effort=s.reasoning_effort,
    )


# ─── JSON 강제 chat + 분류 helper ──────────────────────────────────────────


@dataclass(frozen=True)
class LlmJsonClient:
    """JSON 객체 강제 chat. 파싱 실패는 빈 dict / None 으로 swallow."""

    inner: LlmClient

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 256,
    ) -> dict[str, Any]:
        """JSON object 강제 — dict 반환. 실패 시 빈 dict."""
        try:
            content = self.inner.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                # OpenAI / ollama / vLLM 공통 키. 미지원 백엔드는 무시 → prompt
                # 측에서 JSON-only 지시까지 같이 줘서 양쪽 모두 강제.
                extra={"response_format": {"type": "json_object"}},
            )
        except Exception:
            log.exception("llm chat_json failed")
            return {}
        return parse_json_lenient(content)

    def classify_choice(
        self,
        *,
        system: str,
        user: str,
        choices: list[str],
        field: str = "category",
        extra_keys: tuple[str, ...] = ("reason",),
        max_tokens: int = 256,
        retry_once: bool = True,
    ) -> dict[str, Any] | None:
        """카테고리 분류 helper — choices 중 1개를 골라 dict 반환.

        Returns ``{field: <one of choices>, **extra}`` or ``None`` if both
        the primary call and one retry fail to produce a valid choice.

        ``extra_keys`` 는 LLM 이 같이 출력해야 할 추가 필드 (예: "reason").
        없으면 빈 문자열로 fallback.
        """
        choices_set = frozenset(c.upper() for c in choices)
        schema_hint = (
            f'{{"{field}": '
            + " | ".join(f'"{c}"' for c in choices)
            + (", " + ", ".join(f'"{k}": "..."' for k in extra_keys) if extra_keys else "")
            + "}"
        )
        sys_full = (
            f"{system} 오직 JSON 객체만 출력하세요. 코드펜스/설명/주석 금지. "
            f"스키마: {schema_hint}"
        )

        for attempt in range(2 if retry_once else 1):
            sys_msg = sys_full + (
                f" 출력은 반드시 {len(choices)}개 카테고리 중 1개여야 합니다."
                if attempt > 0
                else ""
            )
            result = self.chat_json(
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
            )
            val = str(result.get(field, "")).strip().upper()
            if val in choices_set:
                out: dict[str, Any] = {field: val}
                for k in extra_keys:
                    out[k] = str(result.get(k, "")).strip()
                return out

        return None


@lru_cache
def get_llm_json_client() -> LlmJsonClient:
    return LlmJsonClient(inner=get_llm_client())


# ─── JSON 파싱 — 코드펜스/접두어 lenient ──────────────────────────────────


def parse_json_lenient(content: str) -> dict[str, Any]:
    """LLM 출력 → dict. 코드펜스/접두어가 섞여 와도 첫 JSON object 추출."""
    s = content.strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl >= 0:
            s = s[nl + 1 :]
    lb, rb = s.find("{"), s.rfind("}")
    if lb >= 0 and rb > lb:
        s = s[lb : rb + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {}
