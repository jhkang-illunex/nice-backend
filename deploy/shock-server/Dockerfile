# shock-server 서비스 — 순수 쇼크 전파 계산 (DB·LLM·Redis 의존 없음).
# nice_shock 패키지만 자급(numpy·networkx·fastapi)이라 src/nice_shock 만 복사 → 초경량.
# 입력 그래프(triple_list)를 클라이언트가 제공하므로 stateless · 수평확장 자유.

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 순수 계산 의존만 (pyproject base 와 버전 정합). DB/LLM/임베딩 드라이버 불필요.
# httpx: tariff 거래비중 조회 (rate_client — backend POST /trade/weight).
RUN pip install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "pydantic>=2.9" \
        "numpy>=2.0" \
        "networkx>=3.3" \
        "httpx>=0.27"

# nice_shock 만 복사 (cross-package 의존 없음 → /app/src 에 단독 배치).
COPY src/nice_shock ./src/nice_shock

RUN useradd --create-home --uid 1000 nice
USER nice

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["uvicorn", "nice_shock.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
