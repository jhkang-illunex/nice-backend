# rag-server 서비스 — PG + LLM/Embed 원격(OpenAI-호환).
# 임베딩 모델을 자체 로딩하지 않으므로 sentence-transformers/torch 불요(EMBED_BASE_URL 사용).
# 자체 로딩이 필요해지면 .[search] extras 활성 + ARG INSTALL_LOCAL_EMBED=true 분기 추가.

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

RUN pip install ".[rag]"

RUN useradd --create-home --uid 1000 nice
USER nice

EXPOSE 8000
CMD ["uvicorn", "nice_rag.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
