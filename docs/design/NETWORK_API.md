# Network API 명세 — `nice_graph` (graph-analysis)

NICE PoC 의 **네트워크 분석 REST API** 입출력 명세. 운영 PG 의
`public.node` / `public.edge` 를 read-only 로 가져와 networkx 로 분석한 결과
를 반환합니다. 동일 정보가 `/docs` (Swagger UI) 와 `/openapi.json` 에도
자동 노출됩니다.

## 베이스 URL

| 환경 | URL |
|---|---|
| dev (로컬 호스트) | `http://localhost:18001` |
| 운영              | `http://<graph 호스트>:18001` |

## 인증

현재 인증 없음 (PoC, 내부망 가정). 운영 노출 시 reverse proxy 의 Basic/JWT
또는 API Gateway 의 mTLS 권장. 코드 수정 불요.

## 데이터 소스

| 테이블 | 사용 컬럼 | 의미 |
|---|---|---|
| `public.node` | `bizno`, `korentrnm`, `engentrnm`, `korreprnm` | 기업 마스터 — 사업자번호 + 한/영 기업명 + 대표명 |
| `public.edge` | `start_bizno → end_bizno`, `trade_year`, `sly_amt`, `trade_cnt`, `taxbll_cnt`, `tamt_amt`, `taxfr_amt` | 기업 간 거래 (방향성) — 세금계산서 기반 |

**운영 31 public 테이블 무수정** — SELECT 만, INSERT/UPDATE/DDL 없음.

## 응답 표준

```http
Content-Type: application/json; charset=utf-8
```

에러 응답은 FastAPI 기본 `{"detail": "..."}` 형식:

```json
{ "detail": "db unreachable: OperationalError" }
```

## 그래프 표현

각 엔드포인트는 호출 시점에 PG 에서 `node`/`edge` 를 SELECT 하고 `networkx.DiGraph`
를 빌드합니다. 빌드 결과:

- **노드** = `bizno` (str), 속성으로 `name_ko/name_en/rep_ko`
- **엣지** = `(start_bizno → end_bizno)`, weight + 거래 메타 5종

가중치(`weight`) 컬럼 선택 가능:

| 값 | 의미 |
|---|---|
| `sly_amt` (default) | 공급가액 |
| `trade_cnt` | 거래 횟수 |
| `taxbll_cnt` | 세금계산서 수 |
| `tamt_amt` | 세액 |
| `taxfr_amt` | 과세표준 |

`weighted=false` 또는 화이트리스트 외 값 → 422 `ValueError`.

## 엔드포인트 요약

| Method | Path | 용도 | 비용 |
|---|---|---|---|
| `GET` | `/health` | 라이브니스 | 무 |
| `GET` | `/health/deep` | PG 도달성 | PG SELECT 1 |
| `GET` | `/api/network/summary` | 그래프 기본 통계 | 그래프 빌드 |
| `GET` | `/api/network/centrality/pagerank` | 가중 PageRank 상위 K | 빌드 + O(N+M) |
| `GET` | `/api/network/centrality/degree` | in/out degree | 빌드 + O(N+M) |
| `GET` | `/api/network/centrality/betweenness` | Betweenness | 빌드 + O(N·M) |
| `GET` | `/api/network/path` | 두 노드 최단 경로 | 빌드 + Dijkstra |
| `GET` | `/api/network/components` | 컴포넌트 분포 | 빌드 + O(N+M) |
| `GET` | `/api/network/neighbors/{bizno}` | N-depth BFS | 빌드 + O(B^d) |

---

## `GET /health` — 라이브니스

### Response 200

```json
{ "status": "ok" }
```

### curl

```bash
curl http://localhost:18001/health
```

---

## `GET /health/deep` — PG 도달성

### Response 200

```json
{ "postgres": "ok" }
```

도달 실패 시:

```json
{ "postgres": "fail: OperationalError" }
```

HTTP 상태는 **항상 200** (운영 디버깅 우선 — fail 정보는 본문 노출).

---

## `GET /api/network/summary` — 그래프 기본 통계

### Request

| 파라미터 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `trade_year` | string | | 거래연도 필터 (예: `"2024"`). None 이면 전체. |

### Response 200

```json
{
  "nodes": 313,
  "edges": 307,
  "density": 0.0031436880478414027,
  "weakly_connected_components": 6,
  "strongly_connected_components": 313,
  "is_dag": true
}
```

응답 필드:

| 필드 | 의미 |
|---|---|
| `nodes`, `edges` | 그래프 크기 |
| `density` | 0~1, `m / (n·(n−1))` |
| `weakly_connected_components` (WCC) | 방향 무시한 연결 부품 수 |
| `strongly_connected_components` (SCC) | 양방향 도달 가능 부품 수 |
| `is_dag` | 순환 없는 DAG 여부 |

### curl

```bash
curl "http://localhost:18001/api/network/summary?trade_year=2024"
```

---

## `GET /api/network/centrality/pagerank`

가중 PageRank 상위 K 정렬. **단일 hub 식별** 에 가장 직관적.

### Request

| 파라미터 | 타입 | 기본 | 제약 |
|---|---|---|---|
| `top_k` | integer | 20 | 1~200 |
| `alpha` | float | 0.85 | 0.1~0.99 (damping factor) |
| `trade_year` | string | null | |
| `weight` | string | `sly_amt` | 화이트리스트 5종 |
| `weighted` | bool | true | false 면 unweighted |

### Response 200

```json
[
  {"bizno": "1130452404", "pagerank": 0.4539059598290988},
  {"bizno": "1280189713", "pagerank": 0.0017503014108041586},
  {"bizno": "7960300058", "pagerank": 0.0017503014108041586}
]
```

### curl

```bash
curl "http://localhost:18001/api/network/centrality/pagerank?top_k=10&trade_year=2024"
```

---

## `GET /api/network/centrality/degree`

in/out degree 합 (`total`) 정렬.

### Response 200

```json
[
  {"bizno": "1130452404", "in": 0.984, "out": 0.0, "total": 0.984},
  {"bizno": "1280189713", "in": 0.0, "out": 0.0032, "total": 0.0032}
]
```

---

## `GET /api/network/centrality/betweenness`

Betweenness centrality — 비용 O(N·M). 1k 노드 정도까진 즉시 응답, 10k+ 부터
는 느려짐.

### Request

| 파라미터 | 기본 | 의미 |
|---|---|---|
| `top_k` | 20 | |
| `normalized` | true | normalized betweenness |
| 나머지 | (pagerank 와 동일) | |

### Response 200

```json
[
  {"bizno": "1130452404", "betweenness": 0.0123}
]
```

---

## `GET /api/network/path` — 가중 Dijkstra 최단 경로

### Request

| 파라미터 | 필수 | 의미 |
|---|---|---|
| `source` | ✓ | 시작 bizno |
| `target` | ✓ | 도착 bizno |
| `trade_year`, `weight`, `weighted` | | 동일 |

### Response 200 (경로 있음)

```json
{
  "found": true,
  "path": ["1418137957", "1130452404"],
  "length": 1017000.0,
  "hops": 1
}
```

### Response 200 (경로 없음 / 노드 없음)

```json
{ "found": false, "reason": "no path" }
```

또는

```json
{ "found": false, "reason": "source '12345' not in graph" }
```

### curl

```bash
curl -G "http://localhost:18001/api/network/path" \
     --data-urlencode "source=1418137957" \
     --data-urlencode "target=1130452404"
```

---

## `GET /api/network/components` — 연결 컴포넌트 통계

### Request

| 파라미터 | 기본 | 의미 |
|---|---|---|
| `top_k` | 5 | 상위 K 컴포넌트 크기 반환 |
| `trade_year` | null | |

### Response 200

```json
{
  "weak_count": 6,
  "weak_largest_size": 308,
  "weak_top_k_sizes": [308, 1, 1, 1, 1],
  "strong_count": 313,
  "strong_largest_size": 1,
  "strong_top_k_sizes": [1, 1, 1, 1, 1]
}
```

---

## `GET /api/network/neighbors/{bizno}` — N-depth BFS 이웃

### Request

| 파라미터 | 위치 | 기본 | 의미 |
|---|---|---|---|
| `bizno` | path | ✓ | 기준 노드 |
| `depth` | query | 1 | 1~4 |
| `direction` | query | `both` | `in` / `out` / `both` |
| `trade_year` | query | null | |

### Response 200

```json
{
  "found": true,
  "source": "1130452404",
  "depth": 1,
  "direction": "both",
  "total_nodes": 308,
  "layers": {
    "0": ["1130452404"],
    "1": ["1418137957", "6368701702", "..."]
  }
}
```

`found=false` 시 RAG 와 같은 패턴:

```json
{ "found": false, "reason": "bizno '12345' not in graph" }
```

### curl

```bash
curl "http://localhost:18001/api/network/neighbors/1130452404?depth=2&direction=both"
```

---

## Status Code 매트릭스

| 코드 | 의미 | 우선 점검 |
|---|---|---|
| `200` | 정상 | — |
| `200 + found:false` | 노드 미존재 / 경로 없음 | bizno 확인 |
| `422 + detail=...` | weight 화이트리스트 위반 / direction 잘못 | `_VALID_WEIGHTS` / `direction in {in,out,both}` |
| `503 + db unreachable: ...` | PG 도달 실패 | `POSTGRES_*`, 네트워크 |

`/health/deep` 로 PG 상태 즉시 확인.

---

## 운영 메타데이터 (현 데이터, 313 nodes × 310 edges)

| 측정 | 값 |
|---|---|
| `/summary` p50 | < 200 ms (PG SELECT + 빌드) |
| `/centrality/pagerank` p50 | < 250 ms |
| `/centrality/betweenness` p50 | ~ 500 ms |
| `/path` p50 | < 200 ms |
| `/neighbors/{bizno}` p50 | < 150 ms |

> ⚠️ 매 호출마다 PG SELECT + 그래프 빌드. 빈번한 호출은 캐시 도입 권장
> (in-process LRU). 5만 노드 진입 시 빌드 ~5s + ~50MB —
> prod 진입 전 캐시 필수.

## Python 클라이언트 SDK 예시

```python
import httpx

class NetworkClient:
    def __init__(self, base_url: str = "http://localhost:18001"):
        self._base = base_url.rstrip("/")
        self._cx = httpx.Client(timeout=60.0)

    def summary(self, trade_year: str | None = None) -> dict:
        r = self._cx.get(f"{self._base}/api/network/summary",
                         params={"trade_year": trade_year})
        r.raise_for_status()
        return r.json()

    def pagerank(self, top_k: int = 20, **opts) -> list[dict]:
        r = self._cx.get(f"{self._base}/api/network/centrality/pagerank",
                         params={"top_k": top_k, **opts})
        r.raise_for_status()
        return r.json()

    def neighbors(self, bizno: str, depth: int = 1, direction: str = "both") -> dict:
        r = self._cx.get(f"{self._base}/api/network/neighbors/{bizno}",
                         params={"depth": depth, "direction": direction})
        r.raise_for_status()
        return r.json()

client = NetworkClient()
s = client.summary("2024")        # {'nodes': 313, 'edges': 307, ...}
top = client.pagerank(top_k=10)   # [{'bizno': '1130452404', 'pagerank': 0.45}, ...]
n = client.neighbors("1130452404", depth=2, direction="both")
```

## OpenAPI / Swagger UI

| 경로 | 내용 |
|---|---|
| `GET /docs`           | Swagger UI (대화형) |
| `GET /redoc`          | ReDoc |
| `GET /openapi.json`   | OpenAPI 3.x JSON |

## 변경 이력

| Date (UTC) | Version | 변경 |
|---|---|---|
| 2026-06-05 | 0.1.0 | 초안 — 7 엔드포인트 + health 2종, networkx 표준 알고리즘 5 wrapper |
