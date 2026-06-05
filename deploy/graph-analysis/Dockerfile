# graph-analysis 서비스 — Neo4j + 시뮬레이션(propagation/matrix/shock).
# search/임베딩/LLM 의존 없음 → 가장 슬림한 이미지.

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

# graph-analysis 는 기본 의존성만(numpy/scipy/networkx 가 base 에 포함).
RUN pip install "."

RUN useradd --create-home --uid 1000 nice
USER nice

EXPOSE 8000
CMD ["uvicorn", "nice_graph.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
