"""company_edge 직접 조회·핸들링 + LLM/ollama 호출 테스트용 IPython 쉘.

``python -m nice_migrate --shell`` 로 진입. engine(sqlalchemy Engine)/pd(pandas)/
requests 가 바로 쓸 수 있는 상태로 IPython 이 열린다. 예:

    In [1]: q("SELECT * FROM company_edge WHERE to_bizno=%(t)s LIMIT 20", t="1234567890")
    In [2]: llm_chat("안녕")                     # LLM_BASE_URL(ollama 등) 연결 테스트
    In [3]: %edit                                # 여러 줄 코드를 에디터(기본 vi)로 작성 후 실행

ipython/requests 는 optional extra(``pip install -e ".[migrate-shell]"``)로만 설치된다 —
company_edge 갱신 자체는 sqlalchemy 만으로 동작하는 독립 CLI 원칙을 유지하기 위함.
"""
from __future__ import annotations

import os

import pandas as pd
import requests
from sqlalchemy import text
from sqlalchemy.engine import Engine

# %edit(멀티라인 편집) 기본 에디터 — 환경변수로 이미 지정돼 있으면 그대로 존중.
os.environ.setdefault("EDITOR", "vi")


def llm_chat(
    prompt: str,
    *,
    messages: list[dict] | None = None,
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
) -> dict:
    """OpenAI 호환 LLM(ollama 등)에 chat completion 요청 — 연결·응답 테스트용.

    ``nice_llm.client.LlmClient.chat`` 과 동일한 호출 모양(엔드포인트/바디/헤더)이지만
    nice_migrate 는 그 패키지를 의존하지 않으므로 여기서 requests 로 직접 재현한다.
    LLM_BASE_URL/LLM_MODEL/LLM_API_KEY/LLM_REASONING_EFFORT/LLM_TIMEOUT_S 환경변수 사용
    (기본값도 nice_llm.settings 와 동일). 원본 응답 dict 그대로 반환(<think> 등 미가공).
    """
    base_url = os.environ.get("LLM_BASE_URL", "http://llm:11434/v1").rstrip("/")
    body: dict = {
        "model": model or os.environ.get("LLM_MODEL", "qwen2.5:7b-instruct"),
        "messages": messages or [{"role": "user", "content": prompt}],
        "temperature": temperature,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    reasoning_effort = os.environ.get("LLM_REASONING_EFFORT", "")
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    r = requests.post(
        f"{base_url}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {os.environ.get('LLM_API_KEY', 'noop')}"},
        timeout=float(os.environ.get("LLM_TIMEOUT_S", "60")),
    )
    r.raise_for_status()
    return r.json()


def start_shell(engine: Engine, *, schema: str = "public") -> None:
    """engine/pd/requests/q/llm_chat 를 노출한 IPython 쉘을 연다. ipython 미설치 시 안내 후 반환."""
    try:
        from IPython import embed
    except ImportError:
        print(
            "[shell] ipython 이 설치돼 있지 않습니다. "
            "`pip install -e \".[migrate-shell]\"` 로 설치 후 다시 실행하세요."
        )
        return

    def q(sql: str, **params) -> pd.DataFrame:
        """SQL 문자열을 DataFrame 으로. 파라미터는 :name 바인딩(sqlalchemy text())."""
        with engine.connect() as c:
            return pd.read_sql(text(sql), c, params=params)

    banner = (
        f"[nice_migrate shell] schema={schema!r} — engine, pd, requests, q(sql, **params), "
        "llm_chat(prompt) 사용 가능.\n"
        "  예: q('SELECT * FROM company_edge WHERE to_bizno=:t LIMIT 20', t='1234567890')\n"
        "  예: llm_chat('안녕')['choices'][0]['message']['content']   "
        "# LLM_BASE_URL(ollama 등) 연결 테스트\n"
        "  예: requests.get(os.environ.get('LLM_BASE_URL','').replace('/v1','') + '/api/tags')  "
        "# ollama 자체 API\n"
        "  %edit 로 멀티라인 편집(기본 에디터 vi, $EDITOR 로 변경 가능)."
    )
    embed(banner1=banner, colors="neutral")
