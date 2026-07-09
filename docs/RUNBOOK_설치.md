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

**코드가 config를 읽고(하드코딩 아님) + compose가 env를 노출** → `.env` 한 파일로 전환된다.
- DB: `nice_common.db.get_pg_engine()` → `POSTGRES_*` (대소문자 무관)
- 임베딩: `EmbedClient(base_url=EMBED_BASE_URL)`
- LLM: `LLM_BASE_URL`

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

# ── 외부 LLM (/agent 엔드포인트에만 필요) ──
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
