# ingestion 잡 컨테이너 — Excel/CSV 파서 + PG/Neo4j dual-write CLI.

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

RUN pip install ".[ingest]"

RUN useradd --create-home --uid 1000 nice
USER nice

# 잡 컨테이너 — `docker compose run --rm ingestion ...` 로 트리거.
# 기본 명령은 도움말. 실제 호출 예:
#   docker compose --profile ingest run --rm ingestion \
#       python -m nice_ingest run hscode --file=/work/관세청_HS부호_20260101.xlsx
CMD ["python", "-m", "nice_ingest", "list"]
