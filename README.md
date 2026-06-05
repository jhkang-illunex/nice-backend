# nice-backend

NICE Open Innovation PoC — 공급망/수요망 충격 시뮬레이션 백엔드.

![CI](https://github.com/jhkang-illunex/nice-backend/actions/workflows/ci.yml/badge.svg)

> 배지는 GitHub repo 가 public 일 때만 외부에서 렌더링됩니다. private 인 경우
> 로그인한 멤버에게만 정상 표시되며, README 를 익명 미러에서 볼 때는 broken
> 이미지로 보일 수 있습니다.

## 현재 상태 — 데이터 수령 대기

| 영역 | 상태 |
|---|---|
| 인프라 (PG/Neo4j/Redis) | docker-compose 기동, healthy |
| PG 스키마 | 8 테이블 + MV 2 + 확장 3 (vector/pg_trgm/btree_gin) 적용 완료 |
| Neo4j 스키마 | 제약 9 + 인덱스 17 + APOC 5.24 적용 완료 |
| Python 모듈 | 1~4주차 (PoC 1차 시연 가능 분량) + 폴리글랏 §5.5 dual_write 구현 + 49 단위 테스트 pass |
| 서비스 분리 | `nice_poc`(코어) / `nice_graph` / `nice_rag` / `nice_ingest` 4 패키지 · 이미지 3개 분리 |
| ETL | 디렉토리 컨벤션 + generic upload(임의 컬럼명 매핑) 둘 다 작동 |
| HSCode RAG | `hsk` 테이블(alembic 0002) + hscode 적재 + BAAI/bge-m3 + RRF hybrid + LLM 에이전트 (코드 완료) |
| **레코드** | **0** — 실 데이터 수령 후 적재 시작 |

**실 데이터가 도착하면** → [docs/DATA_INTAKE.md](docs/DATA_INTAKE.md) 의 6단계
체크리스트 그대로 실행.

## 설계 문서

`docs/` 디렉토리 참조.

원본 설계서 (docx):
- `NICE_폴리글랏_아키텍처_설계서.docx`
- `NICE_Neo4j_그래프모델_설계서_v2.3.docx`
- `NICE_Python_구현명세서.docx`

저장소 내 운영 문서 (md):
- [`ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md) — ADR 5건 (Neo4j 토폴로지 / PG / 패키지명 / DDL 적재 / 의존성)
- [`CSV_SCHEMA.md`](docs/CSV_SCHEMA.md) — 6 도메인 CSV 컬럼 매트릭스
- [`DATA_INTAKE.md`](docs/DATA_INTAKE.md) — 실 데이터 수령 시 6단계 적재 절차
- [`PROGRESS.md`](docs/PROGRESS.md) — 작업 내역 (불변, 4 commit + 49 tests)
- [`POST_INTAKE_TASKS.md`](docs/POST_INTAKE_TASKS.md) — 데이터 도착 후 작업 큐 (P0~P7)
- [`RAG_API.md`](docs/RAG_API.md) — rag-server REST API 입출력 명세 (`/api/hsk/{search,agent}` 등)
- [`NETWORK_API.md`](docs/NETWORK_API.md) — graph-analysis REST API 입출력 명세 (`/api/network/{summary,centrality/*,path,components,neighbors}` 등)
- [`CONTAINERS.md`](docs/CONTAINERS.md) — 두 컨테이너 운영/배포 통합 명세 (env/빌드/운영 명령/트러블슈팅/정책)

## 서버 토폴로지 (RAG + Network)

본 compose 는 **두 개 독립 REST 서비스** 를 정의:
- **rag** (HSCode 검색/에이전트, `rag.hsk` 사용)
- **network** (그래프 분석, `public.node/edge` 사용)

| 영역 | 컴포넌트 | 패키지 / 이미지 | 역할 | GPU |
|---|---|---|---|---|
| **외부 (운영)** | **PostgreSQL** (`172.30.1.101:5433`, NICE 운영) | (외부) | `rag` schema (RAG) + `public.node/edge` (Network) — 운영 31 public 테이블 무수정 | — |
| **rag**         | `redis` (nice-redis)            | `redis:7-alpine`              | RAG 캐시/세션 | — |
|                  | `rag-server`                   | `nice_rag` / `nice/rag-server:dev` | HSCode RAG REST (OpenAI-호환 LLM·임베딩 호출) | — |
|                  | `ingestion` (`ingest` profile) | `nice_ingest` / `nice/ingestion:dev` | hscode → `rag.hsk` 적재 + hsk_embed 임베딩 잡 | — |
|                  | `llm` (옵션, `llm-local`)      | `ollama/ollama` (dev) → `vllm/vllm-openai` (gpu.yml prod) | 자체 LLM (OpenAI `/v1/`) — URL 변경으로 외부 API 전환 | ✓ (gpu.yml 활성 시) |
|                  | `embed` (옵션, `embed-local`)  | `text-embeddings-inference`   | 자체 임베딩 (OpenAI `/v1/embeddings`) — URL 변경으로 외부 API 전환 | — |
| **network**     | `graph-analysis`                | `nice_graph` / `nice/graph-analysis:dev` | `public.node/edge` → networkx 분석 REST | — |

코드는 monorepo 안 4개 패키지로 분리되어 있고, 각 패키지가 한 컨테이너의 진입점:

```
src/
  nice_poc/      # 공용 도메인 코어 (PG 클라이언트, propagation/matrix/shock 등)
  nice_graph/    # graph-analysis 진입점 (network profile)
  nice_rag/      # rag-server (rag profile)
  nice_ingest/   # ingestion CLI + pipelines/{hscode, hsk_embed}
```

Neo4j 는 현 compose 에 미포함 — `nice_graph` 의 현재 데모는 PG 의 node/edge
만으로 동작합니다. Neo4j 기반의 propagation/Leontief 통합은 별 단계.

LLM / 임베딩 백엔드 교체 = `.env` 의 `LLM_BASE_URL` / `EMBED_BASE_URL` 1줄 변경.
자체 호스팅(ollama/vLLM/TEI) ↔ 외부(OpenAI / Anthropic proxy) 가 같은 OpenAI-호환 인터페이스.

## 빠른 시작

```bash
cp .env.example .env

# (a) 전체 띄움 — rag-server + redis + 자체 LLM/Embed
docker compose --profile rag --profile network --profile llm-local --profile embed-local up -d --build

# (b) RAG 코어만 — LLM/Embed 는 외부 API 사용
docker compose --profile rag up -d --build
# 단 .env 의 LLM_BASE_URL / EMBED_BASE_URL 을 외부 URL 로 설정 필요

# (c) 실배포 — GPU 가 있는 prod 호스트 (vLLM)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
               --profile rag --profile network --profile llm-local --profile embed-local up -d --build
# ↑ gpu.yml 은 llm 서비스만 vLLM + GPU 로 교체. rag-server/embed 는 CPU 그대로.
# .env 의 LLM_MODEL 을 HF id 형식으로 갱신 (예: Qwen/Qwen2.5-7B-Instruct).

# (d) ingestion 잡 — HSCode 적재
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hscode --file=/work/hsk.xlsx

# (e) ingestion 잡 — 임베딩 일괄
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hsk_embed --batch-size 32

# (f) 등록 파이프라인 확인
docker compose --profile ingest run --rm ingestion python -m nice_ingest list

# Python 환경 (호스트, 로컬 개발)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ingest,rag]"

# 헬스체크
curl http://localhost:${RAG_API_PORT:-18002}/health          # liveness
curl http://localhost:${RAG_API_PORT:-18002}/health/deep     # + postgres/redis/llm/embed 도달성
```

PostgreSQL 은 **원격 NICE 운영 인스턴스(`172.30.1.101`)** 를 사용합니다 — compose
정의에서 로컬 PG 컨테이너는 제거됨. 따라서 `--profile rag --profile network` 가 띄우는 것은
`redis + rag-server + graph-analysis` 3개입니다.

`.env` 의 PG 관련 변수만 운영 인스턴스를 가리키고 있으면 됩니다:

```env
POSTGRES_HOST=172.30.1.101
POSTGRES_PORT=5432
POSTGRES_USER=nice
POSTGRES_PASSWORD=...
POSTGRES_DB=nice_innovation
```

`deploy/postgres/init/*.sql` 은 더 이상 자동 적용되지 않습니다(로컬 PG 폐기).
운영 PG 의 31 개 기존 테이블/스키마는 **무수정** 이며, 우리 RAG 는 별도
`rag` 스키마에 격리됩니다 (`rag.hsk`, `rag.alembic_version` 등).

이후 스키마 변경은 Alembic 으로 추적합니다 (baseline `0001_baseline` 박힘, ADR-004):

```bash
.venv/bin/alembic current                       # 현재 revision 확인
.venv/bin/alembic revision -m "add column X"    # 신규 마이그레이션 작성
.venv/bin/alembic upgrade head                  # 라이브 DB 적용
```

Neo4j 제약/인덱스는 1회만 수동 적용 (구현명세서 §1 의존 모듈 진입 전):

```bash
docker exec -i nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" < deploy/neo4j/constraints.cypher
docker exec -i nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" < deploy/neo4j/indexes.cypher
```

## 시스템 구현

### 패키지 책임

| 패키지 | 컨테이너 | DBMS | 책임 |
|---|---|---|---|
| `nice_poc`    | (공용)            | —              | 도메인 코어 — propagation/matrix/shock/indicator/safety/result/db 클라이언트 |
| `nice_graph`  | `graph-analysis`  | PostgreSQL (read-only) | `public.node/edge` → networkx 네트워크 분석 REST API (centrality / path / components / neighbors) — 데모 단계 |
| `nice_rag`    | `rag-server`      | PostgreSQL + Redis | HSCode/문서 RAG REST API + LLM/임베딩 클라이언트 — `clients/{llm,embed}` 가 OpenAI-호환 base_url 만 호출 |
| `nice_ingest` | `ingestion`       | PG (rag.hsk) | 잡 컨테이너 CLI + `pipelines/<name>/` 플러그인 (현재: `hscode`, `hsk_embed`) |

### HSCode RAG — 4단계 파이프라인

```
                      ┌────────────────────────────────────────────────────────┐
                      │  관세청_HS부호_xxxxxxxx.xlsx  (12,469 rows × 21 cols)    │
                      └───────────────────────────┬────────────────────────────┘
                                                  │
                          (1차 적재)               │  nice_ingest run hscode --file=...
                                                  ▼
                      ┌────────────────────────────────────────────────────────┐
                      │  PostgreSQL.hsk  ─ hs_code(PK) + 텍스트 + 단가/규격 + ...│
                      │       STORED GENERATED:                                │
                      │         hs2/hs4/hs6 = substr(hs_code, 1, N)            │
                      │         search_text = concat_ws(' | ', 5필드)          │
                      │         search_tsv  = to_tsvector('simple', ...)       │
                      │       embedding vector(1024)  ─── NULL (3차에서 채움)   │
                      └───────────────────────────┬────────────────────────────┘
                                                  │
                          (2차 색인 — 자동)        │  alembic 0002_hsk 이 8개 인덱스 부여
                                                  │  btree(valid_to, hs2/4/6) + GIN(tsv, trgm×2)
                                                  │  + HNSW(embedding vector_cosine_ops)
                                                  ▼
                          (3차 임베딩)             │  nice_ingest run hsk_embed --batch-size 64
                                                  │
                          ┌───────────────────────┴───────────────────────┐
                          ▼                                               ▼
                    embed_documents()                              UPDATE hsk SET embedding=...
                    │   build_document_text(name_ko, std, ...)            │
                    ▼                                                     │
                    EMBED_BASE_URL (TEI / vLLM / OpenAI)                   │
                    BAAI/bge-m3  ─ 1024-d, L2 norm                          │
                                                                          ▼
                      ┌────────────────────────────────────────────────────────┐
                      │  hsk.embedding 12,469 rows × vector(1024)  채움 완료     │
                      └───────────────────────────┬────────────────────────────┘
                                                  │
                          (4차 검색/에이전트)       │  GET /api/hsk/{search,agent}
                                                  ▼
                      ┌────────────────────────────────────────────────────────┐
                      │  hsk_index.search_hybrid  ─ 단일 SQL CTE 안 RRF        │
                      │      vec rank (embedding <=> qvec)                     │
                      │    + trg rank (search_text <-> qtext)                  │
                      │    + ts  rank (ts_rank with plainto_tsquery)           │
                      │      score = Σ 1/(rrf_k + rank_i)                      │
                      └───────────────────────────┬────────────────────────────┘
                                                  │
                                                  ▼
                      [/api/hsk/search] → list[HskHit]
                      [/api/hsk/agent]  → LLM_BASE_URL (Qwen2.5-7B 등) → HskAnswer + citations
```

### 백엔드 추상화 — 환경변수 1줄로 swap

`nice_rag/clients/{llm,embed}.py` 는 httpx 한 줄로 `{base_url}/chat/completions`
또는 `{base_url}/embeddings` 만 호출 — 백엔드 식별 코드/SDK 분기 없음.

| 백엔드 | URL | LLM_MODEL 표기 | GPU |
|---|---|---|---|
| ollama (dev 기본)             | `http://llm:11434/v1`           | `qwen2.5:7b-instruct` (tag)        | ✗ 로컬 CPU |
| vLLM (prod, gpu.yml override) | `http://llm:11434/v1`           | `Qwen/Qwen2.5-7B-Instruct` (HF id) | ✓ 필수 |
| 외부 OpenAI                   | `https://api.openai.com/v1`     | `gpt-4o-mini` 등                   | — |
| 외부 LiteLLM proxy            | `https://proxy.example.com/v1`  | proxy 매핑 따름                     | — |

| 임베딩 백엔드 | URL | EMBED_MODEL | GPU |
|---|---|---|---|
| TEI CPU (PoC 기본, profile embed-local)  | `http://embed:8080/v1` | `BAAI/bge-m3` (XLM-R 기반, 1024-d) | ✗ CPU 충분 |
| TEI GPU (대용량 적재 시)                  | `http://embed:8080/v1` | 동일                         | ✓ (옵션) |
| 외부 OpenAI                              | `https://api.openai.com/v1` | `text-embedding-3-large` 등 | — |

**GPU 가 실제 본질적으로 필요한 컴포넌트는 LLM 추론(7B+) 뿐**입니다. rag-server 는
임베딩 자체 로딩을 안 하고(원격 호출), embed(TEI) 는 0.6B 모델이라 CPU 로도 PoC
처리량 (~50-150 docs/s) 이 나옵니다. `docker-compose.gpu.yml` 은 `llm` 만 vLLM 으로
바꿔 GPU 를 부착하며, rag/embed 의 GPU 부착은 의도적으로 비웠습니다.

### 멱등성/재실행 보장

| 단계 | 멱등 메커니즘 |
|---|---|
| 1차 적재 (hscode)         | `INSERT ... ON CONFLICT (hs_code) DO UPDATE` — 같은 Excel 재실행 안전 |
| 3차 임베딩 (hsk_embed)    | 기본 `only_missing=true` (embedding IS NULL row 만) — 중단 시 자연 재개. `--rebuild` 로 전체 재임베딩 |
| 알렘빅                    | `0001_baseline` → `0002_hsk` 선형 + downgrade 정의 — 롤백 가능 |
| 트리거                    | `updated_at` 자동 갱신 (변경 추적) |

## CLI 레퍼런스

### `nice_ingest` — 데이터/임베딩 잡

```bash
# 등록된 파이프라인 나열
docker compose --profile ingest run --rm ingestion python -m nice_ingest list
#   hscode      관세청 HS부호 xlsx → pg.hsk (1차 적재; 색인/임베딩은 별 단계)
#   hsk_embed   hsk.search_text → 임베딩 백엔드(EMBED_MODEL) → hsk.embedding (UPDATE)

# 1차 적재 — Excel → hsk
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hscode \
    --file=/work/관세청_HS부호_20260101.xlsx \
    [--active-only] [--dry-run]

# 3차 임베딩 — search_text → embedding
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hsk_embed \
    [--batch-size 64] [--rebuild] [--limit N] [--dry-run]
```

옵션 매트릭스:

| 파이프라인 | 옵션 | 의미 |
|---|---|---|
| `hscode`    | `--file PATH`     | (필수) Excel 경로 — 컨테이너 안 `/work/` 가 호스트 루트 |
| `hscode`    | `--active-only`   | `valid_to >= today` 인 row 만 적재 |
| `hscode`    | `--dry-run`       | DB 미접속, 파싱/통계만 |
| `hsk_embed` | `--batch-size N`  | 임베딩 API 1회 호출 텍스트 수 (TEI CPU 32~128 권장) |
| `hsk_embed` | `--rebuild`       | 기존 `embedding` 도 재임베딩 (기본은 NULL row 만) |
| `hsk_embed` | `--limit N`       | 디버깅용 처리 상한 |
| `hsk_embed` | `--dry-run`       | UPDATE 미실행, 임베딩 호출까지만 |

### 새 파이프라인 추가 (firms / supplies / trade 등)

```
src/nice_ingest/pipelines/<name>/
├── __init__.py    # register(Pipeline(name, description, add_args, run))
└── pipeline.py    # add_args(parser) + run(ns) -> int
```

`registry._autoload()` 가 `pkgutil.iter_modules` 로 자동 발견 — CLI 수정 불요.

### `alembic` — 스키마 마이그레이션

```bash
# 컨테이너 안에서 실행 (ingestion 이미지에 alembic 포함)
docker compose --profile ingest run --rm ingestion alembic current
docker compose --profile ingest run --rm ingestion alembic upgrade head
docker compose --profile ingest run --rm ingestion alembic downgrade -1

# 신규 revision 작성 (호스트의 venv 에서 작업 후 git add)
.venv/bin/alembic revision -m "add column X"

# offline 모드 — 라이브 DB 없이 raw DDL 만 출력 (검수용)
.venv/bin/alembic upgrade head --sql
```

### API — `rag-server` (rag)

```bash
# 1) 헬스 — pg/redis/llm/embed 도달성 한 번에
curl http://localhost:18002/health/deep

# 2) HSCode 검색 — 키워드 → RRF(vec + trgm + ts) → 후보 리스트
curl "http://localhost:18002/api/hsk/search?q=농가+사육용+말&limit=5"

# 3) HSCode 자연어 에이전트 — 검색 + LLM 한국어 요약/근거 인용
curl "http://localhost:18002/api/hsk/agent?q=경주마+수입할+때+적용되는+HS코드&k=5"
```

응답 status code 의미:

| code | 원인 | 해결 |
|---|---|---|
| 200  | 정상                                        | — |
| 200 + `"후보 없음"` 메시지 | 검색 결과 0건                | 쿼리 조정 또는 임베딩 적재 확인 |
| 501  | (이번 단계에 없음 — 모든 stub 가 본체로 활성됨) | — |
| 503  | embed 백엔드 불통                          | `embed` 컨테이너 / `EMBED_BASE_URL` 확인 |
| 503  | llm 백엔드 불통                            | `llm` 컨테이너 / `LLM_BASE_URL` 확인 |
| 503  | hsk 테이블 미마이그레이션 / DB 불통          | `alembic upgrade head` 또는 `nice-pg` healthy 확인 |

### API — `graph-analysis` (별도 인프라 운영 시)

```bash
curl http://localhost:18001/health/deep                          # neo4j/pg 도달성
curl http://localhost:18001/api/scenarios                        # 시나리오 리스트
curl -X POST http://localhost:18001/api/runs -d '{...}'          # 시뮬 트리거
curl "http://localhost:18001/api/firms/{firm_id}/network"        # drill-down
```

엔드포인트 전체 목록은 `nice_poc.api.routers` 의 라우터별 `prefix` 참조.

### end-to-end 운영 흐름 (4 명령)

```bash
# 0) 인프라 + 자체 LLM/Embed
docker compose --profile rag --profile network --profile embed-local --profile llm-local up -d --build

# 1) hsk 테이블 생성
docker compose --profile ingest run --rm ingestion alembic upgrade head

# 2) Excel → hsk (12,469 row)
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hscode --file=/work/관세청_HS부호_20260101.xlsx

# 3) hsk.search_text → embedding (TEI CPU 기준 2~4분)
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hsk_embed --batch-size 64

# 4) 검증
curl "http://localhost:18002/api/hsk/agent?q=농가+사육용+말&k=5"
```

## 디렉토리 구조

```
.
├── docs/                       # 설계서 (docx) + ADR
├── alembic/versions/           # 0001_baseline, 0002_hsk, ...
├── deploy/
│   ├── postgres/init/          # docker entrypoint 가 자동 적용
│   ├── neo4j/                  # 수동 적용 cypher
│   ├── graph-analysis/         # Dockerfile (slim, base deps)
│   ├── rag-server/             # Dockerfile (.[rag])
│   └── ingestion/              # Dockerfile (.[ingest] — openpyxl)
├── src/nice_poc/               # 공용 도메인 코어 (변경 없음)
│   ├── config/                 # pydantic-settings (.env 로딩)
│   ├── db/                     # Neo4j / PG / Redis 클라이언트
│   ├── api/                    # FastAPI 라우터 (graph-analysis 가 재사용)
│   ├── data/                   # Neo4j → DataFrame + scipy.sparse
│   ├── matrix/ shock/ propagation/ indicator/ safety/
│   ├── estimate/               # 보조 ML (extras=ml)
│   ├── result/                 # impact_table + :IMPACTS 적재
│   └── etl/                    # 기존 CSV 적재 (firms/supplies/trade/...)
├── src/nice_graph/             # graph-analysis 진입점
│   └── api/main.py             # nice_poc 라우터 마운트
├── src/nice_rag/               # rag-server
│   ├── config.py               # RagSettings (LLM/EMBED_BASE_URL 등)
│   ├── clients/{llm,embed}.py  # OpenAI-호환 base_url 클라이언트
│   ├── search/{hsk_embed,hsk_index}.py  # 임베딩 헬퍼 + RRF hybrid SQL
│   └── api/routers/{health,hsk}.py      # /health, /api/hsk/{search,agent}
├── src/nice_ingest/            # 잡 CLI + 플러그인 파이프라인
│   ├── __main__.py registry.py
│   └── pipelines/
│       ├── hscode/             # Excel → pg.hsk
│       └── hsk_embed/          # search_text → embedding
├── tests/
├── docker-compose.yml          # 7 services × 5 profiles
├── docker-compose.gpu.yml      # rag/llm/embed GPU override
├── pyproject.toml              # hatch packages 4개 + [rag]/[ingest] extras
└── .env.example                # LLM/EMBED_BASE_URL 등 전체
```

`cache/`(PoC 2차), `sync/`(운영 1.0), `search/`(운영 1.1) 은 phase 진입 시
신설합니다. ADR-003 참조.

## ETL — 원천 → PG/Neo4j 적재

```bash
# 디렉토리 레이아웃
<root>/
├── firms.csv              # firm_id, biz_no, rep_bizno, firm_name, sector_code, base_year, sales_year_fin, ...
├── supplies.csv           # source_id, target_id, year, amount, source_cate, target_cate, purchase_weight, sales_weight, ...
├── trade.csv              # firm_id, hs6, iso_alpha2, year, direction(EXP/IMP), amount, weight_hs, weight_nation, rank
└── masters/
    ├── sectors.csv        # code, name, level, parent_code, color
    ├── hs_codes.csv       # code, name, hs2, hs4, elasticity
    └── countries.csv      # iso_alpha2, name_kr, name_en

# 실행 (적재 순서가 강제됨: masters → firms → supplies → trade)
python -m nice_poc.etl masters  <root>
python -m nice_poc.etl firms    <root>
python -m nice_poc.etl supplies <root>
python -m nice_poc.etl trade    <root>

# 또는 한 번에
python -m nice_poc.etl all      <root>
```

어댑터를 교체하면 원천을 바꿀 수 있습니다 — `src/nice_poc/etl/sources/` 의
Protocol 을 만족하면 됩니다(예: `PostgresRawSource` 가 kis_em_* 등 RAW 테이블에서
직접 읽도록).

모든 적재는 MERGE/UPSERT 로 idempotent — 동일 입력 재실행 안전.

### 임의 CSV 업로드 (컬럼명이 다를 때)

원천 CSV 의 컬럼명이 도메인 파이프라인과 다르면 `upload-pg` / `upload-neo4j`
서브커맨드 + `--rename` 매핑 사용. 자세한 컬럼 명세는 `docs/CSV_SCHEMA.md`.

```bash
# 한국어 컬럼명을 표준 컬럼명으로 매핑하면서 PG 에 UPSERT
python -m nice_poc.etl upload-pg /data/raw_firms.csv \
    --table firms --pk firm_id \
    --rename "기업ID=firm_id,기업명=firm_name,업종=sector_code,재무매출=sales_year_fin"

# 임의 Cypher 적용 (노드 + 관계 동시)
python -m nice_poc.etl upload-neo4j /data/raw_firms.csv \
    --cypher-file /tmp/firms_merge.cypher \
    --rename "기업ID=firm_id,기업명=firm_name"

# 실제 적재 전 행수 + 컬럼 누락 검증만
python -m nice_poc.etl upload-pg /data/raw_firms.csv \
    --table firms --pk firm_id --dry-run
```

## 12주 마일스톤 (Python 구현명세서 §10)

| 주차 | 모듈 | 산출물 |
|---|---|---|
| 1 | `data/`, `matrix/matrix_H.py`, `safety/spectral_radius.py` | H 생성, ρ(H) 측정 |
| 2 | `shock/`, `propagation/bicgstab.py`, `propagation/leontief.py` | 단일 시나리오 Δx |
| 3 | `safety/max_delta_cap.py`, `indicator/edge_value.py`, `tis.py` | 안전장치 + 화면 지표 |
| 4 | `result/to_neo4j.py`, `result/aggregate.py`, `data/load_graph.py` | :IMPACTS 적재 + 시연 가능 |
| 5 | `matrix/matrix_R.py`, `matrix_B.py` | α 슬라이더 |
| 6 | `propagation/shortest_path.py`, `indicator/network_cri.py` | 한주동 + CRI 가중 |
| 7 | `estimate/sales_estimator.py`, `asset_estimator.py` | ML 결측 보완 |
| 8 | `tests/` 전체 + 부하 테스트 | 검증 보고서 |
