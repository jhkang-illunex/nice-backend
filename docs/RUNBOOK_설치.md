# 런북 — rag / shock 컨테이너 설치·기동 (개별 시스템 이식)

> docker-compose.yml + 개별 도커 이미지를 새 시스템에 옮겨, `.env` 설정만 바꿔 기동하는 절차.

---

## 0. 요약 — "이미지 + compose + .env 만 바꾸면 바로 되나?"

| 서비스 | 즉시 실행? | 전제 |
|---|---|---|
| **shock-server** | ✅ **즉시** | 없음 (stateless·DB-free, triple_list 입력). 이미지만 있으면 끝 |
| **rag-server** | ⚠️ **조건부** | 컨테이너는 `.env`만 바꾸면 기동·외부연결되나, **외부 DB에 RAG 데이터(`hsk` 테이블 + 임베딩)가 적재돼 있어야** 검색 결과가 나옴 |

→ **답: shock 은 그대로 바로 됨. rag 는 "설정만"으로는 컨테이너가 뜨고 외부 DB/임베딩에 붙지만,
DB에 hsk 데이터가 없으면 검색이 빔.** 새(빈) DB면 §5의 적재를 1회 해야 한다. 기존에 적재된
DB를 가리키면 그대로 동작.

외부 연결(DB·임베딩·LLM)은 **전부 env(아규먼트)로 처리 가능** — 코드 하드코딩 없음(§3).

---

## 1. 반입물 (연결 구간에서 준비 → 매체로 이동)

- `docker-compose.yml`, `.env` (아래 §3 값 채운 것)
- **이미지**: `nice/rag-server`, `nice/shock-server` (+ 자체 호스팅 시 `redis:7-alpine`,
  `text-embeddings-inference`, `ollama/ollama`)
- (air-gap) LLM/임베딩 모델 볼륨 사전적재 — [`README.md`](../README.md) §현행 배포-E 에어갭 체크리스트

### 이미지 반입 (레지스트리 없을 때)
```bash
# (연결 구간) 저장
docker save -o nice-images.tar nice/rag-server:dev nice/shock-server:dev \
  redis:7-alpine ghcr.io/huggingface/text-embeddings-inference:cpu-1.6 ollama/ollama:latest
# (대상 시스템) 적재
docker load -i nice-images.tar
```
> 태그가 compose의 `${APP_TAG:-dev}` 와 일치해야 `docker compose up` 이 **빌드 없이** 그 이미지를 쓴다.
> (이미지가 없으면 compose가 build 를 시도 → 소스 없으면 실패. 반드시 load 먼저.)

---

## 2. shock-server (즉시)
```bash
docker compose --profile shock up -d shock-server
curl http://localhost:${SHOCK_API_PORT:-8004}/health          # {"status":"ok"}
```
DB·임베딩·LLM 불요. `/api/shock/{tariff,volume,propagate}`, `/api/cri` 즉시 사용.

---

## 3. rag-server 외부 연결 설정 (`.env`)

**rag 는 외부 3종에 붙는다** (전부 env·하드코딩 아님):

| 의존 | env | 필수도 | 쓰이는 곳 |
|---|---|---|---|
| **PostgreSQL**(pgvector) | `POSTGRES_*` | 필수 | 검색 대상 데이터(hsk) |
| **임베딩 서버** | `EMBED_BASE_URL` | **필수** | `/search`·`/agent` 질의 벡터화 (불통 시 503) |
| **LLM** | `LLM_BASE_URL` | /agent 필수 · /search 사용 | `/search` 질의추출·CRAG(폴백 degrade) / `/agent` 답변 |
| Redis | `REDIS_*` | rag 프로파일이 자동 기동 | 검색로그 등 |

> 즉 **임베딩과 LLM 둘 다** 붙는다. 임베딩은 검색 필수(없으면 503), LLM은 /agent 필수이며
> /search 에서도 품목추출·CRAG 로 호출된다(불통이면 폴백으로 degrade). 근거(코드):
> `nice_common.db.get_pg_engine()`→`POSTGRES_*`, `EmbedClient(base_url=EMBED_BASE_URL)`,
> `extract_goods`/`_crag_correct`→`LLM_BASE_URL`.

```env
# ── 외부 PostgreSQL (pgvector) ──
POSTGRES_HOST=<외부DB호스트>
POSTGRES_PORT=5432
POSTGRES_USER=<user>
POSTGRES_PASSWORD=<pw>
POSTGRES_DB=<dbname>

# ── 외부 임베딩 서버 (OpenAI-호환 /v1/embeddings) ──
EMBED_BASE_URL=http://<임베딩서버>:<port>/v1
EMBED_MODEL=BAAI/bge-m3        # 서버 모델에 맞게
EMBED_DIM=1024                 # 모델 차원 (bge-m3=1024)
EMBED_API_KEY=noop             # 필요 시

# ── 외부 LLM ──  ※ /agent 필수 + /search 도 사용(질의 품목추출·CRAG)
#   /search: LLM 불통 시 폴백(원 질의·fit)으로 degrade는 되나, 정확도 위해 연결 권장.
#   /agent : LLM 필수(답변 생성).
LLM_BASE_URL=http://<LLM서버>:<port>/v1
LLM_MODEL=<모델>

# ── Redis (rag 프로파일이 자동 기동; 외부 쓰면 아래 지정) ──
REDIS_HOST_INTERNAL=redis      # 자체 컨테이너면 기본값 유지
```

### 기동
```bash
# 외부 임베딩·LLM 사용(자체 컨테이너 안 띄움): rag 프로파일만
docker compose --profile rag up -d
# 자체 임베딩/LLM 도 띄우려면 프로파일 추가
docker compose --profile rag --profile embed-local --profile llm-local up -d
```

---

## 4. 데이터 전제 (rag 만 해당)

rag 검색이 값을 내려면 **외부 DB에 hsk 데이터 + 임베딩**이 있어야 한다.
- **기존 적재 DB를 가리키면 → 이 단계 skip** (바로 검색됨).
- **빈/새 DB면 → 1회 적재**:
```bash
# (1) hsk 테이블 스키마
docker compose --profile ingest run --rm ingestion alembic upgrade head
# (2) HS코드 적재
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hscode --file=/work/관세청_HS부호_YYYYMMDD.xlsx
# (3) 임베딩 채우기
docker compose --profile ingest run --rm ingestion \
    python -m nice_ingest run hsk_embed --batch-size 64
```

> shock 은 데이터 전제 없음. (단, 데모/company_edge 기반 작업을 병행한다면
> `company_edge.trade_rate` 는 [`RUNBOOK_trade_rate_갱신.md`](RUNBOOK_trade_rate_갱신.md) 로 채운다.)

### 4-B. RAG 데이터 백업 / 복원 (nice_ingest 재적재 대신 통째로 이관)

RAG 데이터는 **`rag` 스키마**에 있다: `hsk`(HS코드+임베딩, 12,469행), `hs_heading`, `synonyms`,
`alembic_version`, `search_log`(런타임 로그). 임베딩 재계산(nice_ingest) 없이 **덤프로 이관** 가능.

**필요 확장(대상 DB)**: `vector`, `pg_trgm`, `btree_gin` (덤프 헤더에 `CREATE EXTENSION IF NOT
EXISTS` 로 포함됨).

> **대상 PG를 `nice/postgres:pg16` 베이스 이미지로 띄우면** 이 확장 3종이 **기본 생성**돼 있어
> 복원이 바로 된다 (vector 0.8.2 = NICE 운영 DB 동일 버전).
> ```bash
> docker build -t nice/postgres:pg16 deploy/postgres/        # 정의: deploy/postgres/
> docker run -d -e POSTGRES_PASSWORD=<pw> -e POSTGRES_DB=nice_innovation \
>   -v pgdata:/var/lib/postgresql/data -p 5432:5432 nice/postgres:pg16
> ```
> template1 에도 만들어 두므로 이후 `CREATE DATABASE` 로 만드는 DB 도 확장을 상속한다.

**백업 (pg_dump, schema+data)** — 로컬에 `postgres:16`/`pgvector:pg16` 이미지 이용:
```bash
mkdir -p backups
printf -- "CREATE EXTENSION IF NOT EXISTS vector;\nCREATE EXTENSION IF NOT EXISTS pg_trgm;\nCREATE EXTENSION IF NOT EXISTS btree_gin;\n\n" > backups/rag_backup.sql
docker run --rm --network host -e PGPASSWORD='<pw>' postgres:16 \
  pg_dump -h <DB호스트> -p <포트> -U <user> -d <db> \
  --schema=rag --no-owner --no-privileges \
  --exclude-table=rag.hsk_backup_20260611 \
  --exclude-table-data=rag.search_log \
  >> backups/rag_backup.sql
gzip -k backups/rag_backup.sql   # 전송용 압축(.gz)
```
> 제외: 구백업 테이블(`hsk_backup_*`) 전체, `search_log` **데이터**(스키마는 유지).

**복원 (대상 DB에 업로드)**:
```bash
# 대상 DB 준비 (없으면)
psql -h <대상DB> -U <user> -c "CREATE DATABASE <db>;"
# 복원 (확장은 덤프 헤더가 자동 생성)
gunzip -c backups/rag_backup.sql.gz | psql -h <대상DB> -U <user> -d <db>
#   또는 비압축: psql ... -d <db> -f backups/rag_backup.sql
```

**복원 검증**:
```sql
SELECT count(*) AS 행, count(embedding) AS 임베딩 FROM rag.hsk;   -- 12469 / 12469
SELECT count(*) FROM pg_indexes WHERE schemaname='rag' AND tablename='hsk';  -- 9
```

> 현재 스냅샷(이관용)은 `backups/rag_backup.sql`(≈139MB) / `.sql.gz`(≈44MB) 에 생성돼 있다.
> **대용량이라 git 에는 커밋 안 함**(`.gitignore` 처리). 임시 pgvector 컨테이너 복원으로 무결성
> 검증 완료(에러 0, 12,469행·인덱스 9개).

---

## 5. 검증

```bash
# shock
curl http://localhost:8004/health
curl -X POST http://localhost:8004/api/cri -H 'Content-Type: application/json' -d @cri_sample.json

# rag — 의존성 4종(postgres/redis/llm/embed) 도달성 한 번에
curl http://localhost:8002/health/deep
# 실제 검색
curl "http://localhost:8002/api/hsk/search?q=밸브&limit=5"
```
`/health/deep` 이 postgres/redis/llm/embed 각각 `ok` 면 외부 연결 정상.

---

## 6. 흔한 함정

| 증상 | 원인 | 조치 |
|---|---|---|
| `docker compose up` 이 build 시도 후 실패 | 이미지 미적재 | §1 `docker load` 먼저 |
| rag 검색 결과 0건 | DB에 hsk 임베딩 미적재 | §4 적재 |
| `/health/deep` embed=fail | EMBED_BASE_URL 도달 불가/모델 차원 불일치 | URL·`EMBED_DIM` 확인 |
| `/health/deep` postgres=fail | POSTGRES_* 오설정/방화벽 | 값·네트워크 확인 |
| (air-gap) embed/llm 컨테이너 모델 다운로드 시도 | 모델 볼륨 미적재 | README 에어갭 체크리스트 |

---

## 7. 정리

- **shock**: 이미지 적재 → `up` → 끝 (무의존).
- **rag**: 이미지 적재 → `.env`(DB·임베딩·LLM 외부값) → `up` → **DB에 hsk 데이터 있으면 즉시,
  없으면 §4 적재 1회**.
- 외부 연결은 전부 env로 처리되므로 **소스/코드 변경 없이 `.env` 만으로 이식** 가능.
