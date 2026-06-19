# Containers — 운영/배포 명세

본 PoC 의 모든 컨테이너에 대한 **운영자/DevOps 관점** 통합 명세. 외부
호출자용 API 명세는 [`RAG_API.md`](RAG_API.md) / [`NETWORK_API.md`](NETWORK_API.md)
참조.

대상 청중: PoC 를 배포하거나 사고에 대응하는 운영자.

---

## 1. 컨테이너 매트릭스

| 컨테이너 | 이미지 | profile | 진입점 | 호스트 포트 | 의존 | restart |
|---|---|---|---|---|---|---|
| `nice-rag-server` | `nice/rag-server:${APP_TAG:-dev}` | `rag` | `uvicorn nice_rag.api.main:app --host 0.0.0.0 --port 8000` | `${RAG_API_PORT:-18002}:8000` | redis(internal) + postgres(external) + llm/embed(internal or external) | `unless-stopped` |
| `nice-graph-analysis` | `nice/graph-analysis:${APP_TAG:-dev}` | `network` | `uvicorn nice_graph.api.main:app --host 0.0.0.0 --port 8000` | `${GRAPH_API_PORT:-18001}:8000` | postgres(external) | `unless-stopped` |
| `nice-redis` | `redis:7-alpine` | `rag` | `redis-server --maxmemory 2gb --maxmemory-policy allkeys-lru --appendonly no` | `${REDIS_PORT:-6379}:6379` | (없음) | `unless-stopped` |
| `nice-ingestion` | `nice/ingestion:${APP_TAG:-dev}` | `ingest` | (compose `run` 마다 CMD 지정) | (포트 없음 — 잡 컨테이너) | postgres(external) + embed(internal or external) | `no` (oneshot) |
| `nice-llm` | `ollama/ollama:latest` (dev) / `vllm/vllm-openai:latest` (prod gpu.yml) | `llm-local` | image entrypoint | `${LLM_PORT:-11434}:11434` | (없음) | `unless-stopped` |
| `nice-embed` | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.6` | `embed-local` | `--model-id ${EMBED_MODEL} --port 8080 --max-batch-tokens 16384` | `${EMBED_PORT:-18080}:8080` | (없음, HF Hub 다운로드) | `unless-stopped` |

**볼륨**:

| 볼륨 | 마운트 | 용도 | 보존 정책 |
|---|---|---|---|
| `redis-data` | nice-redis:/data | Redis 데이터(AOF off 라 사실상 휘발) | 무관 |
| `llm-models` | nice-llm:/root/.ollama | ollama 모델 캐시 (~400MB ~ 4GB) | 보존 권장 (재다운로드 회피) |
| `embed-models` | nice-embed:/data | HF 모델 캐시 (BGE-M3 ~2GB) | 보존 권장 |
| `./:/work:ro` | nice-ingestion | 호스트의 데이터(Excel 등) read-only 마운트 | 호스트 파일 |

**네트워크**: 모든 컨테이너가 `nice-net` (bridge) 에 attach. 컨테이너 끼리
서비스명(`redis`, `llm`, `embed`)으로 도달, 외부(`postgres`) 는 원격 IP 로
직접 도달.

---

## 2. 환경변수 전체

`.env` 파일이 single source of truth. 빠진 변수는 compose 의 default 값
사용. 비밀번호 등 시크릿은 `.env` (gitignore) 에만 두고 commit 금지.

### 인프라

| 변수 | 기본 | 영향 컨테이너 | 의미 | 변경 시 |
|---|---|---|---|---|
| `POSTGRES_HOST` | `172.30.1.101` | rag-server, graph-analysis, ingestion | 원격 PG host | recreate |
| `POSTGRES_PORT` | `5432` | 위와 같음 | PG port | recreate |
| `POSTGRES_USER` | `nice` | 위와 같음 | PG user | recreate |
| `POSTGRES_PASSWORD` | `nice` | 위와 같음 | PG password (시크릿) | recreate |
| `POSTGRES_DB` | `nice_innovation` | 위와 같음 | PG database | recreate |
| `REDIS_HOST_INTERNAL` | `redis` | rag-server | compose 내부 alias | recreate |
| `REDIS_PORT_INTERNAL` | `6379` | rag-server | compose 내부 포트 | recreate |
| `REDIS_PORT` | `6379` | redis (호스트 노출) | 호스트 → redis | restart |
| `REDIS_DB` | `0` | rag-server | Redis DB index | recreate |

### LLM 백엔드 (OpenAI-호환)

| 변수 | 기본 | 의미 | 표기 |
|---|---|---|---|
| `LLM_BASE_URL` | `http://llm:11434/v1` | LLM API base URL | dev: ollama. prod GPU: 동일 포트 vLLM. 외부: OpenAI/Anthropic proxy URL |
| `LLM_MODEL` | `qwen2.5:7b-instruct` | 모델 ID | ollama: `qwen2.5:0.5b-instruct` 등 tag. vLLM: `Qwen/Qwen2.5-7B-Instruct` 등 HF id. OpenAI: `gpt-4o-mini` |
| `LLM_API_KEY` | `noop` | API 키 | 자체 호스팅엔 `noop`, 외부 API 사용 시 실 키 |
| `LLM_TIMEOUT_S` | `60` | 호출 timeout | 큰 모델/긴 답변엔 ↑ |
| `LLM_GPU_MEMORY_UTIL` | `0.85` | vLLM 전용 GPU 메모리 비율 | gpu.yml 활성 시만 |

### 임베딩 백엔드 (OpenAI-호환 /v1/embeddings)

| 변수 | 기본 | 의미 |
|---|---|---|
| `EMBED_BASE_URL` | `http://embed:8080/v1` | dev/prod: TEI, 외부: OpenAI URL |
| `EMBED_MODEL` | `BAAI/bge-m3` | HF model id |
| `EMBED_API_KEY` | `noop` | 자체 호스팅 무관 |
| `EMBED_DIM` | `1024` | 출력 차원 (DB 의 vector(1024) 와 정확히 일치해야 함) |
| `EMBED_TIMEOUT_S` | `30` | 호출 timeout |
| `EMBED_NORMALIZE` | `true` | L2 정규화 (코사인 유사도용) |
| `EMBED_QUERY_INSTRUCTION` | `` | query 측 prefix. BGE-M3: 빈 문자열. Qwen3: `"Instruct: ..."` |

### 호스트 노출 포트

| 변수 | 기본 | 의미 |
|---|---|---|
| `RAG_API_PORT` | `18002` | rag-server 외부 노출 |
| `GRAPH_API_PORT` | `18001` | graph-analysis 외부 노출 |
| `LLM_PORT` | `11434` | llm 외부 노출 (디버깅) |
| `EMBED_PORT` | `18080` | embed 외부 노출 (디버깅) |

### 앱

| 변수 | 기본 | 의미 |
|---|---|---|
| `APP_ENV` | `local` | dev/prod 등 (현재 단순 로깅에만 사용) |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARN/ERROR |
| `APP_TAG` | `dev` | 이미지 태그 (`nice/rag-server:${APP_TAG}`) |

`recreate` = `docker compose up -d --force-recreate <service>`, `restart` = `docker compose restart <service>`.

---

## 3. 빌드 명세

### `deploy/rag-server/Dockerfile`

```
FROM python:3.11-slim
└─ apt: ca-certificates, curl
└─ COPY pyproject.toml + src/
└─ pip install ".[rag]"   ← httpx, sqlalchemy 등 (rag extras 는 현재 비어있음)
└─ USER nice (uid 1000)
CMD: uvicorn nice_rag.api.main:app
```

- 이미지 크기 실측: **~250 MB** (python:3.11-slim + 의존성)
- 캐시 전략: pyproject.toml 변경 없으면 의존성 layer 재사용 → 코드만 바뀌면 빌드 < 5s
- multi-arch: amd64/arm64 모두 가능 (순수 Python 의존성)

### `deploy/graph-analysis/Dockerfile`

```
FROM python:3.11-slim
└─ apt: ca-certificates, curl
└─ COPY pyproject.toml + src/
└─ pip install "."   ← base 의존성만 (networkx 포함, ml extras 없음)
└─ USER nice
CMD: uvicorn nice_graph.api.main:app
```

- 이미지 크기 실측: **~280 MB** (numpy/scipy/pandas/networkx 포함)
- amd64/arm64 모두 가능

### `deploy/ingestion/Dockerfile`

```
FROM python:3.11-slim
└─ apt: ca-certificates, curl
└─ COPY pyproject.toml + src/
└─ pip install ".[ingest]"   ← openpyxl 추가
└─ USER nice
CMD: python -m nice_ingest list
```

- 이미지 크기 실측: **~270 MB**
- compose run 마다 CMD 를 override 해서 사용 (`nice_ingest run hscode ...`)

### 빌드 명령

```bash
# 단일 서비스 재빌드 + recreate
docker compose --profile rag build rag-server
docker compose --profile rag up -d --force-recreate rag-server

# 전체 재빌드
docker compose --profile rag --profile network --profile ingest build
```

빌드 캐시 무효화 (의존성 충돌 의심 시):

```bash
docker compose --profile rag build --no-cache rag-server
```

---

## 4. 운영 명령 모음

### 기동 / 정지

```bash
# RAG + Network 둘 다 + 자체 LLM/Embed
docker compose --profile rag --profile network --profile llm-local --profile embed-local up -d --build

# RAG 만
docker compose --profile rag up -d --build

# Network 만
docker compose --profile network up -d --build

# 전체 정지 (컨테이너 제거, 볼륨 보존)
docker compose --profile rag --profile network --profile llm-local --profile embed-local down

# 볼륨까지 모두 삭제
docker compose --profile rag --profile network --profile llm-local --profile embed-local down -v
```

### 로그

```bash
# 실시간 tail
docker compose logs -f rag-server
docker compose logs -f --tail 100 graph-analysis

# 모든 컨테이너 + 시간순
docker compose logs -f --timestamps
```

### Shell 진입 (디버깅)

```bash
docker compose exec rag-server bash       # 가동 중
docker compose exec rag-server python -c "from nice_rag.config import get_rag_settings; print(get_rag_settings())"

# 또는 새 컨테이너로
docker compose --profile ingest run --rm ingestion bash
```

### 상태 점검

```bash
docker compose ps
docker ps --filter "name=nice-"

# 헬스체크
curl http://localhost:18002/health/deep   # rag-server
curl http://localhost:18001/health/deep   # graph-analysis
```

### ingestion 잡 실행

```bash
# 사용 가능한 파이프라인 나열
docker compose --profile ingest run --rm ingestion python -m nice_ingest list

# HSCode 적재
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hscode --file=/work/hsk.xlsx

# 임베딩 일괄 (TEI CPU 기준 ~25 분)
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hsk_embed --batch-size 32

# 마이그레이션
docker compose --profile ingest run --rm ingestion alembic current
docker compose --profile ingest run --rm ingestion alembic upgrade head
```

### LLM 모델 관리 (ollama)

```bash
# 모델 pull
docker exec nice-llm ollama pull qwen2.5:0.5b-instruct
docker exec nice-llm ollama pull qwen2.5:7b-instruct

# 설치된 모델 나열
docker exec nice-llm ollama list

# 모델 삭제 (디스크 회수)
docker exec nice-llm ollama rm <model>
```

### 데이터 cleanup

```bash
# ollama 모델 캐시 비우기
docker volume rm nice-backend_llm-models

# TEI 모델 캐시 비우기
docker volume rm nice-backend_embed-models

# Redis 비우기
docker exec nice-redis redis-cli FLUSHDB
```

---

## 5. 트러블슈팅 매트릭스

본 PoC 사이클 중 **실제로 만난** 이슈 기반.

### 5-1. `/health/deep` 의 `postgres: fail`

| 증상 | 원인 | 진단 | 해결 |
|---|---|---|---|
| `fail: OperationalError` | password mismatch | rag-server env 의 `POSTGRES_PASSWORD` vs PG 실제값 | `.env` 갱신 후 `docker compose up -d --force-recreate rag-server` |
| `fail: AmbiguousParameter` | SQL 의 named param 타입 추론 실패 | `CAST(:p AS text)` 누락 | SQL 에 명시 CAST 추가 |
| `fail: ConnectError` | `POSTGRES_HOST` 가 잘못 가리킴 | `docker exec nice-rag-server getent hosts $POSTGRES_HOST` | host/port 확인 |
| `fail: relation "rag.hsk" does not exist` | 마이그레이션 미적용 | `psql ... -c "\dn"` 에 `rag` 없음 | `docker compose --profile ingest run --rm ingestion alembic upgrade head` |

### 5-2. `/health/deep` 의 `embed: fail`

| 증상 | 원인 | 진단 | 해결 |
|---|---|---|---|
| `fail: ConnectError` | embed 컨테이너 미가동 | `docker ps --filter name=nice-embed` | `docker compose --profile embed-local up -d embed` |
| `fail: TimeoutException` | 모델 로딩 중 | `docker logs nice-embed` 에 "Downloading" / "Loading" | 2~5분 대기 (BGE-M3 ~2GB) |
| `embed_backend unreachable` 응답 | URL 잘못됨 | rag-server env `EMBED_BASE_URL` 확인 | `.env` 갱신 |

### 5-3. `/health/deep` 의 `llm: fail`

| 증상 | 원인 | 진단 | 해결 |
|---|---|---|---|
| `fail: ConnectError` | llm 컨테이너 미가동 또는 외부 API URL 잘못 | `docker ps`, `curl $LLM_BASE_URL/models` | 컨테이너 기동 또는 URL 갱신 |
| `200 + 빈 응답` | ollama 모델 미설치 | `docker exec nice-llm ollama list` | `ollama pull <model>` |

### 5-4. ingestion 실패

| 증상 | 원인 | 해결 |
|---|---|---|
| `file not found: /work/관세청_*.xlsx` | 한글 파일명 unicode normalization 차이 | `ln -sf 관세청_*.xlsx hsk.xlsx` 후 `/work/hsk.xlsx` |
| `psycopg.errors.InvalidObjectDefinition: generation expression is not immutable` | STORED GENERATED 에 STABLE 함수 사용 | `to_tsvector('simple'::regconfig, ...)` / `concat_ws` → `\|\| coalesce` 패턴 |
| `413 Payload Too Large` (TEI) | batch_size 가 TEI 의 `--max-client-batch-size` (기본 32) 초과 | `--batch-size 32` 로 줄임 |
| `extension "vector" is not available` | OS 패키지 미설치 | DBA 에게 `apt install postgresql-XX-pgvector` 요청 |
| `permission denied to create extension "pg_trgm"` | DB-level CREATE 권한 부족 | DBA 에게 `GRANT CREATE ON DATABASE ... TO nice` 요청 |

### 5-5. RRF 검색 결과 이상

| 증상 | 원인 | 해결 |
|---|---|---|
| 정확 쿼리가 1위 안 나옴 | 임베딩 미적재 | `SELECT count(embedding) FROM rag.hsk` 가 적재 row 수와 일치 확인 |
| 모든 결과 score 가 비슷 | RRF 의 vec 시그널만 동작, trgm/ts 없음 | `pg_trgm` 확장 활성 여부 확인 |
| 짧은 한글 쿼리에 빈 결과 | tsvector 가 `'simple'` 토크나이저 라 한국어 형태소 안 잡음 | trigram 만 매칭됨 — 정상. 정확도 향상은 mecab_ko 도입 시 |

### 5-6. 네트워크 분석 (graph-analysis) 응답 이상

| 증상 | 원인 | 해결 |
|---|---|---|
| `weight=...` 가 422 | 화이트리스트 외 컬럼 | `sly_amt / trade_cnt / taxbll_cnt / tamt_amt / taxfr_amt` 중에서 |
| `found: false, source not in graph` | bizno 가 node 테이블에 없거나 edge 에 없는 isolated node | `SELECT * FROM public.node WHERE bizno=...` |
| `betweenness` 가 매우 느림 | O(N·M) — 큰 그래프 | top_k 줄이거나 캐시 추가 |

---

## 6. 운영 정책

### 로그

- 모든 컨테이너 stdout/stderr → docker 의 default json-file driver
- 보존 정책 미설정 — prod 진입 시 `logging.options.max-size` / `max-file` 설정 권장 (예: `max-size=10m, max-file=3`)
- 외부 수집 (Loki/CloudWatch) 미연동 — PoC 단계

### 메트릭

- 현재 노출 없음 — `/health/deep` 만이 사실상 메트릭 역할
- prod 진입 시: FastAPI Prometheus middleware 또는 OpenTelemetry exporter 추가 권장
- LLM 호출 latency / 임베딩 호출 latency / RRF SQL latency 가 핵심 지표

### 시크릿

- `POSTGRES_PASSWORD`, `LLM_API_KEY`, `EMBED_API_KEY` 가 시크릿 후보
- 현재: `.env` 파일 (gitignore) — PoC 수준
- prod 진입 시: AWS Secrets Manager / Vault / K8s Secret 으로 이관 권장
- `docker compose config` 가 시크릿을 평문 노출 — 운영 중엔 호스트 권한 분리

### 백업

- PG: **운영 DBA 가 관리** — 본 PoC 는 read-only 사용이라 백업 책임 없음
- Redis: AOF off, maxmemory-policy=allkeys-lru → 데이터 손실 허용
- ollama/TEI 모델 캐시: 재다운로드 가능 → 백업 불필요

### 의존성 업데이트

- `pyproject.toml` 의 의존성 변경 시 모든 앱 컨테이너 재빌드 필요
- Dockerfile 의 base 이미지(`python:3.11-slim`) 패치는 분기별 점검 권장

### 운영 PG 무수정 정책 ★

- **모든 우리 SQL 은 read-only SELECT 또는 `rag.*` schema 한정 INSERT/UPDATE**
- public schema 의 31 운영 테이블은 절대 손대지 않음
- alembic 의 `version_table_schema='rag'` 가 그 격리 보장
- 사이클 후 검증: `pg_tables WHERE schemaname='public'` 의 baseline diff 가 0 임을 확인

### 컨테이너 lifecycle

- `restart: unless-stopped` — 호스트 재부팅 후 자동 복귀
- `ingestion` 만 `restart: "no"` — 잡 컨테이너라 의도적
- 운영 중 컨테이너 제거: `docker compose stop <svc> && docker compose rm <svc>`. 볼륨은 별도 결정.

---

## 변경 이력

| Date (UTC) | 변경 |
|---|---|
| 2026-06-05 | 초안 — 6 컨테이너 매트릭스, env 전체, 빌드/운영/트러블슈팅/정책 |
