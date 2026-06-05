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
| ETL | 디렉토리 컨벤션 + generic upload(임의 컬럼명 매핑) 둘 다 작동 |
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

## 서버 토폴로지 (2 호스트)

| 서버 | 컴포넌트 | 패키지 / 이미지 | 역할 | GPU |
|---|---|---|---|---|
| **server1** (graph)    | `neo4j` (nice-neo4j)            | `neo4j:5.24-community`        | 그래프 본질 (Firm, SUPPLIES, Scenario, drill-down) | — |
|                         | `graph-analysis`                | `nice_graph` / `nice/graph-analysis:dev` | 임팩트 전파 REST API (Leontief / BiCGStab) | — |
| **server2** (data/RAG) | `postgres` (nice-pg, pgvector)  | `pgvector/pgvector:pg16`      | 결과/색인/벡터 + HSK / 마스터 적재처 | — |
|                         | `redis` (nice-redis)            | `redis:7-alpine`              | KPI/좌표/시계열 + RAG 캐시 | — |
|                         | `rag-server`                    | `nice_rag` / `nice/rag-server:dev` | HSCode/문서 RAG REST (OpenAI-호환 LLM·임베딩 호출) | ✓ (실배포) |
|                         | `ingestion`                     | `nice_ingest` / `nice/ingestion:dev` | Excel/CSV → PG + Neo4j dual-write 잡 (plugin pipelines) | — |
|                         | `llm` (옵션)                    | `ollama/ollama`               | 자체 LLM (OpenAI `/v1/`) — URL 변경으로 외부 API 전환 | ✓ (실배포) |
|                         | `embed` (옵션)                  | `text-embeddings-inference`   | 자체 임베딩 (OpenAI `/v1/embeddings`) — URL 변경으로 외부 API 전환 | ✓ (실배포) |

코드는 monorepo 안 4개 패키지로 분리되어 있습니다:

```
src/
  nice_poc/      # 공용 도메인 코어 (propagation, matrix, shock, db, etl, ...)
  nice_graph/    # graph-analysis 진입점 (nice_poc 재사용)
  nice_rag/      # rag-server (config + clients/{llm,embed} + api/routers/hsk)
  nice_ingest/   # ingestion CLI + pipelines/<name>/ 플러그인 (현재 hscode)
```

LLM / 임베딩 백엔드 교체 = `.env` 의 `LLM_BASE_URL` / `EMBED_BASE_URL` 1줄 변경.
자체 호스팅(ollama/vLLM/TEI) ↔ 외부(OpenAI / Anthropic proxy) 가 같은 OpenAI-호환 인터페이스.

## 빠른 시작

```bash
cp .env.example .env

# (a) 로컬 dev — 인프라 + 앱 + 자체 LLM/Embed 전부 한 호스트에서
docker compose --profile server1 --profile server2 \
               --profile llm-local --profile embed-local up -d --build

# (b) 로컬 dev — 인프라 + 앱만 (LLM/Embed 는 외부 URL 사용)
docker compose --profile server1 --profile server2 up -d --build

# (c) 실배포 server1 (graph 박스)
docker compose --profile server1 up -d --build

# (d) 실배포 server2 (data/RAG 박스, CPU, 외부 LLM/Embed)
docker compose --profile server2 up -d --build

# (e) 실배포 server2 (data/RAG 박스, GPU, 자체 LLM/Embed)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml \
               --profile server2 --profile llm-local --profile embed-local \
               up -d --build

# (f) ingestion 잡 — HSCode 1차 적재 (구현 후)
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hscode --file=/work/관세청_HS부호_20260101.xlsx

# (g) ingestion 잡 — 등록 파이프라인 확인
docker compose --profile ingest run --rm ingestion python -m nice_ingest list

# Python 환경 (호스트, 로컬 개발)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,ingest]"

# 헬스체크
curl http://localhost:${GRAPH_API_PORT:-18001}/health        # server1
curl http://localhost:${GRAPH_API_PORT:-18001}/health/deep
curl http://localhost:${RAG_API_PORT:-18002}/health          # server2
curl http://localhost:${RAG_API_PORT:-18002}/health/deep     # + llm/embed 도달성
```

PostgreSQL 스키마는 컨테이너 최초 기동 시 `deploy/postgres/init/*.sql` 이
자동 적용됩니다. 재적용이 필요하면 볼륨 삭제 후 재기동:

```bash
docker compose --profile server2 down -v && docker compose --profile server2 up -d
```

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

## 디렉토리 구조

```
.
├── docs/                       # 설계서 (docx) + ADR
├── deploy/
│   ├── postgres/init/          # docker entrypoint 가 자동 적용
│   └── neo4j/                  # 수동 적용 cypher
├── src/nice_poc/
│   ├── config/                 # pydantic-settings (.env 로딩)
│   ├── db/                     # Neo4j / PG / Redis 클라이언트
│   ├── api/                    # FastAPI 진입점 + 라우터
│   ├── data/                   # Neo4j → DataFrame + scipy.sparse
│   ├── matrix/                 # A / A1 / H / R / B 행렬
│   ├── shock/                  # 8종 시나리오 Δy
│   ├── propagation/            # BiCGSTAB / Sparse LU / 한주동 BFS
│   ├── indicator/              # Edge Value / TIS / Network CRI
│   ├── safety/                 # ρ(A) / row-normalize / cap / hub
│   ├── estimate/               # 보조 ML (extras=ml)
│   └── result/                 # impact_table + :IMPACTS 적재
├── tests/
├── docker-compose.yml
├── pyproject.toml
└── .env.example
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
