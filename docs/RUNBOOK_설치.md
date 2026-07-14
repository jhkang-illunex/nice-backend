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
- **3개 개별 번들** (필요한 것만 반입):

| 번들 | 내용 | 크기 | 언제 |
|---|---|---|---|
| `nice_ai_app.tar.gz` | 이미지: rag-server·shock-server·ingestion | ≈320MB | **항상** |
| `nice_ai_embed.tar.gz` | TEI 이미지 + **bge-m3 모델** | ≈1.5GB | 임베딩 자체호스팅 시 |
| `nice_ai_llm.tar.gz` | ollama 이미지 + **qwen3:14b(q4_K_M) 모델** | ≈12GB | LLM 자체호스팅 시 |

> **PostgreSQL 은 별도 제공**(외부 DB) — 번들에 미포함. 자체 PG 가 필요하면 `deploy/postgres/`
> (nice/postgres:pg16, vector/pg_trgm/btree_gin 기본) 를 별도 빌드·반입.
> 임베딩·LLM 을 **외부(내부망) 엔드포인트**로 붙이면 embed/llm 번들도 불필요(URL만 지정).

> ⚠ **LLM(qwen3:14b) 은 GPU 필수** — 16GB GPU 전제. nvidia runtime 이 없어도 컨테이너는
> CPU 로 뜨지만 14B 추론이 초당 수 토큰 이하라 실사용 불가. 실전 서버는 **NVIDIA 드라이버 +
> nvidia-container-toolkit** 설치 후 `gpu-ollama.yml` override 로 기동(아래 §기동). 임베딩은
> CPU 전용(TEI, GPU 불요).  → GPU 환경 구성: [`RUNBOOK_GPU_docker.md`](RUNBOOK_GPU_docker.md).

### 오프라인 적재

```bash
# (대상 시스템, 인터넷 불필요) — 앱은 항상
docker load -i nice_ai_app.tar.gz
docker images | grep -E "nice/"

# 임베딩 자체호스팅 (bge-m3): 이미지 load + 모델 볼륨 복원
tar -xzf nice_ai_embed.tar.gz               # → tei-*.image.tar, bge-m3.model.tar, RESTORE.txt
docker load -i tei-cpu-1.6.image.tar
docker run --rm -v nice-backend_embed-models:/v -v "$PWD":/in alpine tar xf /in/bge-m3.model.tar -C /v

# LLM 자체호스팅 (qwen3:14b): 동일 패턴  ※ GPU 필수(§기동 참조)
tar -xzf nice_ai_llm.tar.gz                 # → ollama.image.tar, qwen3-14b.model.tar, RESTORE.txt
docker load -i ollama.image.tar
docker run --rm -v nice-backend_llm-models:/v -v "$PWD":/in alpine tar xf /in/qwen3-14b.model.tar -C /v
```
> 각 embed/llm 번들 안에 `RESTORE.txt`(적재·실행 명령)가 들어 있다. **LLM 은 GPU 필수** — 아래 §기동.

**에어갭 실행 3원칙** (검증됨 — 이미지들 `--network none` 기동 확인):
1. **반드시 `docker load` 먼저**, 그다음 `docker compose ... up -d` (이미지 있으면 빌드/pull 안 함).
2. **`--build` 금지** — `build`/`up --build` 는 pip·apt 를 인터넷에서 받으려다 실패.
   태그가 compose `${APP_TAG:-dev}=dev` 와 일치해야 로드본을 그대로 쓴다.
3. **모델은 이미지가 아니라 볼륨** — embed/llm 은 모델을 `*-models` 볼륨에 **먼저 복원**해야
   첫 실행 때 인터넷에서 안 받는다(위 tar 복원). 안 하면 에어갭에서 모델 다운로드 시도 → 실패.

> 런타임 인터넷 의존 없음 검증: `docker run --network none nice/shock-server:dev`(→/health ok),
> `nice/rag-server:dev`(→부팅 ok), `nice/postgres:pg16`(→확장 자동생성).

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
```

### 기동
```bash
# 외부 임베딩·LLM 사용(자체 컨테이너 안 띄움): rag 프로파일만
docker compose --profile rag up -d

# 자체 임베딩만(CPU) + 외부 LLM
docker compose --profile rag --profile embed-local up -d

# 자체 임베딩(CPU) + 자체 LLM(qwen3:14b, GPU 필수) — gpu-ollama.yml override 로 GPU 예약
docker compose -f docker-compose.yml -f docker-compose.gpu-ollama.yml \
  --profile rag --profile embed-local --profile llm-local up -d
```
> ⚠ **LLM(llm-local)** 은 반드시 `-f docker-compose.gpu-ollama.yml` 를 붙여 GPU 로 기동한다.
> 이 override 없이(base compose 만) 띄우면 ollama 가 **CPU 로 뜨는데 qwen3:14b 는 실사용 불가**.
> 반대로 override 를 붙였는데 **nvidia-container-toolkit 미설치**면
> `could not select device driver "" with capabilities: [[gpu]]` 로 기동 실패
> → 먼저 [`RUNBOOK_GPU_docker.md`](RUNBOOK_GPU_docker.md) 로 드라이버·toolkit 설치.
> GPU 사용 확인: `docker logs nice-llm 2>&1 | grep -i offload` (`offloaded N/N layers to GPU`).

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

# rag — 의존성 3종(postgres/llm/embed) 도달성 한 번에
curl http://localhost:8002/health/deep
# 실제 검색
curl "http://localhost:8002/api/hsk/search?q=밸브&limit=5"
```
`/health/deep` 이 postgres/llm/embed 각각 `ok` 면 외부 연결 정상.

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
