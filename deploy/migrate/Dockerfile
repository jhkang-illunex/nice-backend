# migrate 잡 컨테이너 — company_edge.trade_rate/sell_rate/buy_rate 갱신 CLI(nice_migrate).
# nice_migrate 는 sqlalchemy 만 의존하는 독립 CLI(다른 nice_* 패키지 미의존, tests/test_nice_migrate.py
# ::test_migrate_is_db_driver_only 로 검증됨) → src/nice_migrate 만 복사, pyproject 전체 설치 안 함
# (nice-poc 베이스 의존인 neo4j·fastapi·scipy 등은 이 이미지에 불필요 → 가볍게 유지).
# --shell(IPython+pandas 데이터 조회, requests 로 LLM/ollama 호출 테스트)까지 이미지에 포함해
# 에어갭에서 pip install 없이 바로 사용.

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

# vim-tiny 가 /usr/bin/vi 제공 — --shell 의 IPython %edit 기본 에디터.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl vim-tiny \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# nice_migrate 코드 자체 의존(sqlalchemy/psycopg/pandas) + --shell 전용(ipython/requests) —
# pyproject 의 base/migrate-shell extra 와 버전 정합. `pip install .` 을 안 쓰는 이유는 위 주석 참고.
RUN pip install \
        "sqlalchemy>=2.0" \
        "psycopg[binary,pool]>=3.2" \
        "pandas>=2.2" \
        "ipython>=8.26" \
        "requests>=2.32"

COPY src/nice_migrate ./src/nice_migrate

RUN useradd --create-home --uid 1000 nice
USER nice

# 잡 컨테이너 — `docker compose --profile migrate run --rm migrate python -m nice_migrate ...`
# 기본 명령은 도움말(compose 예시는 docs/RUNBOOK_trade_rate_갱신.md §2-컨테이너).
CMD ["python", "-m", "nice_migrate", "--help"]
