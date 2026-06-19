# demo 서비스 — Streamlit 데모 (HS → 시드 → 3차 확장 → LLM → 쇼크).
# 같은 compose network 의 rag-server / graph-analysis 를 HTTP 로 호출하고,
# kis_ra.s_ra604 와 public.node/edge 는 PG read-only SELECT 로 직접 조회.

ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install ".[demo]"

RUN useradd --create-home --uid 1000 nice
USER nice

EXPOSE 8000
CMD ["streamlit", "run", "src/nice_demo/app.py", \
     "--server.port=8000", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
