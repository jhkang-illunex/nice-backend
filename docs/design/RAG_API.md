# RAG API 명세 — `nice_rag` (rag-server)

NICE PoC 의 HSCode RAG REST API 입출력 명세. FastAPI 기반이라 동일 정보가
`/docs` (Swagger UI) 와 `/openapi.json` 에도 자동 노출됩니다.

## 베이스 URL

| 환경 | URL |
|---|---|
| dev (로컬 호스트) | `http://localhost:18002` |
| 운영 (server2)    | `http://<server2 호스트>:18002` |

## 인증

현재 인증 없음 (PoC, 내부망 가정). 운영 노출 시 reverse proxy 의 Basic/JWT
또는 API Gateway 의 mTLS 적용 권장. rag-server 본체 코드 수정 불요.

## 응답 표준

```http
Content-Type: application/json; charset=utf-8
```

에러 응답은 FastAPI 기본 `{"detail": "..."}` 형식:

```json
{ "detail": "embed backend unreachable (http://embed:8080/v1): ConnectError" }
```

## 엔드포인트 요약

| Method | Path | 용도 | 의존성 |
|---|---|---|---|
| `GET` | `/health` | 라이브니스 | (없음) |
| `GET` | `/health/deep` | 의존성 4종 도달성 | postgres + redis + llm + embed |
| `GET` | `/api/hsk/search` | 키워드/의미 검색 (RRF hybrid) | postgres + embed |
| `GET` | `/api/hsk/agent` | 자연어 질의 → LLM 답변 + 인용 | postgres + embed + llm |

---

## `GET /health` — 라이브니스

프로세스 응답 가능 여부. 외부 의존성 점검 없음.

### Request

(없음)

### Response 200

```json
{ "status": "ok" }
```

### curl

```bash
curl http://localhost:18002/health
```

---

## `GET /health/deep` — 의존성 도달성

PG / Redis / LLM / Embed 4종 도달 여부를 한 번에. 각 필드 값 = `"ok"` 또는
`"fail: {ExceptionClass}"`. **HTTP 상태는 항상 200** — 운영 디버깅 용도라
의존성 일부 실패해도 본문은 정상 반환.

### Request

(없음)

### Response 200 (성공)

```json
{
  "postgres": "ok",
  "redis": "ok",
  "llm": "ok",
  "embed": "ok"
}
```

### Response 200 (의존성 일부 실패 예)

```json
{
  "postgres": "ok",
  "redis": "ok",
  "llm": "fail: ConnectError",
  "embed": "ok"
}
```

### curl

```bash
curl http://localhost:18002/health/deep
```

---

## `GET /api/hsk/search` — HSCode hybrid 검색

키워드를 받아 **3 시그널 RRF 결합** 으로 검색:

1. **vec** — 임베딩 (`embedding <=> qvec`, pgvector cosine)
2. **trg** — pg_trgm (`search_text <-> q`, n-gram distance)
3. **ts**  — tsvector (`plainto_tsquery` 토큰 매칭)

단일 SQL CTE 안에서 결합 — `score = Σ 1/(60 + rank_i)`. 3 시그널 모두 rank=1
이면 score ≈ **0.0492** (이론적 최대 = 정확 매칭 강한 신호).

### Request

| 파라미터 | 타입 | 필수 | 제약 | 의미 |
|---|---|---|---|---|
| `q` | string | ✓ | `1 ≤ len ≤ 200` | 검색 키워드 (한국어/영문 자유) |
| `limit` | integer | | `1 ≤ n ≤ 50`, default 10 | 반환 후보 수 |

### Response 200

```json
[
  {
    "hs_code": "0101211000",
    "name_ko": "농가 사육용",
    "name_en": "For farm breeding",
    "description": "농가 사육용 |  | (말) | For farm breeding | ",
    "score": 0.0492
  },
  {
    "hs_code": "0121229803",
    "name_ko": "말",
    "name_en": "Kelp meal",
    "description": "말 |  |  | Kelp meal | ",
    "score": 0.0315
  }
]
```

응답 필드:

| 필드 | 타입 | 의미 |
|---|---|---|
| `hs_code` | string | 10 자리 zero-padded HS 부호 |
| `name_ko` | string\|null | 한글 품목명 |
| `name_en` | string\|null | 영문 품목명 |
| `description` | string\|null | 임베딩/trigram 색인 대상 텍스트 (5 필드 결합) |
| `score` | float | RRF 결합 점수. **0.0492 ≈ 정확 매칭 신호** |

### Response 422 — 요청 검증 실패

```json
{ "detail": [...FastAPI 의 ValidationError 배열...] }
```

### Response 503 — 의존성 도달 실패

| detail 패턴 | 의미 |
|---|---|
| `embed backend unreachable (...): {Exc}` | TEI/vLLM/OpenAI 등 임베딩 백엔드 도달 실패 |
| `hsk search failed — table not migrated or DB unreachable: {Exc}` | PG 도달 실패 또는 `rag.hsk` 미마이그레이션 |

### curl

```bash
curl -G "http://localhost:18002/api/hsk/search" \
     --data-urlencode "q=농가 사육용 말" \
     --data-urlencode "limit=5"
```

---

## `GET /api/hsk/agent` — 자연어 질의 + LLM 답변

자연어 질의 → hybrid 검색 (`k` 건) → LLM 컨텍스트 wrap → 한국어 답변. LLM
이 부실해도 `citations[0]` 이 보통 정답 — **citations 가 ground truth**.

### Request

| 파라미터 | 타입 | 필수 | 제약 | 의미 |
|---|---|---|---|---|
| `q` | string | ✓ | `1 ≤ len ≤ 500` | 자연어 질의 (한국어 권장) |
| `k` | integer | | `1 ≤ n ≤ 20`, default 5 | LLM 컨텍스트로 제공할 후보 수 |

### Response 200

```json
{
  "answer": "경주말은 HS 0101291000 을 사용합니다.",
  "citations": [
    {
      "hs_code": "0101291000",
      "name_ko": "경주말",
      "name_en": "Horses for racing",
      "description": "경주말 |  | (말) | Horses for racing | ",
      "score": 0.0328
    }
  ]
}
```

응답 필드:

| 필드 | 타입 | 의미 |
|---|---|---|
| `answer` | string | LLM 생성 한국어 답변. 후보 0 건 시 고정 메시지. |
| `citations` | HskHit[] | 검색 결과 k 건. 환각 검증/근거 표시용. |

### Response 200 (후보 없음 — 고정 메시지)

```json
{
  "answer": "해당 질의에 매칭되는 HS 부호 후보를 찾지 못했습니다.",
  "citations": []
}
```

### Response 422 / 503

`/search` 와 동일. 추가로 LLM 도달 실패 시 503:

| detail 패턴 | 의미 |
|---|---|
| `llm backend unreachable (...): {Exc}` | LLM 백엔드(ollama/vLLM/OpenAI) 도달 실패 |

### curl

```bash
curl -G "http://localhost:18002/api/hsk/agent" \
     --data-urlencode "q=경주마를 수입할 때 어떤 HS 코드를 사용하나요?" \
     --data-urlencode "k=5"
```

---

## Status Code 매트릭스 — 디버깅 가이드

| 코드 | 의미 | 우선 점검 |
|---|---|---|
| `200` | 정상 | — |
| `422` | 요청 파라미터 검증 실패 | `q` 길이 / `limit`·`k` 범위 |
| `503` + `embed backend unreachable` | TEI / vLLM / OpenAI 임베딩 도달 실패 | `EMBED_BASE_URL`, embed 컨테이너 |
| `503` + `llm backend unreachable` | ollama / vLLM / OpenAI LLM 도달 실패 | `LLM_BASE_URL`, llm 컨테이너 |
| `503` + `hsk search failed ...` | PG 도달 또는 마이그레이션 누락 | `POSTGRES_*`, `alembic upgrade head` |

`/health/deep` 로 어느 의존성인지 즉시 분간 가능.

---

## 운영 메타데이터 (실측)

| 측정 | 값 | 비고 |
|---|---|---|
| `/api/hsk/search` p50 | < 100 ms | RRF SQL 단일 round-trip, HNSW 인덱스 |
| `/api/hsk/search` p99 | < 300 ms | embed 호출 포함 |
| `/api/hsk/agent` p50 | 1~3 s | LLM 응답 latency 지배 (모델 크기에 따라) |
| 임베딩 dim | 1024 | BAAI/bge-m3 |
| 적재 row 수 | 12,469 | `rag.hsk` (관세청 HS 부호 2026-01-01 기준) |
| 정확 매칭 score | ≈ 0.0492 | 3 시그널 모두 rank=1 결과 |

## 클라이언트 SDK 예시 (Python)

```python
import httpx

class HskClient:
    def __init__(self, base_url: str = "http://localhost:18002"):
        self._base = base_url.rstrip("/")
        self._cx = httpx.Client(timeout=30.0)

    def search(self, q: str, limit: int = 10) -> list[dict]:
        r = self._cx.get(f"{self._base}/api/hsk/search",
                         params={"q": q, "limit": limit})
        r.raise_for_status()
        return r.json()

    def agent(self, q: str, k: int = 5) -> dict:
        r = self._cx.get(f"{self._base}/api/hsk/agent",
                         params={"q": q, "k": k})
        r.raise_for_status()
        return r.json()

client = HskClient()
hits = client.search("농가 사육용 말", limit=5)
ans = client.agent("경주마를 수입할 때 HS 코드는?")
```

## OpenAPI / Swagger UI

| 경로 | 내용 |
|---|---|
| `GET /docs`           | Swagger UI (대화형 시도 가능) |
| `GET /redoc`          | ReDoc (가독성 우선 문서) |
| `GET /openapi.json`   | OpenAPI 3.x JSON (codegen 입력) |

## 변경 이력

| Date (UTC) | Version | 변경 |
|---|---|---|
| 2026-06-05 | 0.1.0 | 초안 — `/health`, `/health/deep`, `/api/hsk/{search,agent}` |
