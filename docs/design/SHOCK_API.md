# Shock API 명세 — `nice_graph` (graph-analysis)

NICE PoC 의 **외생 충격 시나리오 REST API** 입출력 명세. HS 코드 기반으로
관련 기업을 시드로 잡아 거래 그래프를 N 차 확장하고, LLM 으로 1 차 충격
대상 기업을 선정한 뒤, 거듭제곱급수 합으로 쇼크를 전파합니다. 동일 정보가
`/docs` (Swagger UI) 와 `/openapi.json` 에도 자동 노출됩니다.

## 베이스 URL

| 환경 | URL |
|---|---|
| dev (로컬 호스트) | `http://localhost:18001` |
| 운영              | `http://<graph 호스트>:18001` |

## 인증

현재 인증 없음 (PoC, 내부망 가정). 운영 노출 시 reverse proxy / API Gateway
사용 권장. 코드 수정 불요.

## 데이터 소스

| 테이블 | 사용 컬럼 | 의미 |
|---|---|---|
| `public.origin_kis_em__s_em001` | `bizno, upchecd, korentrnm, korreprnm, mainpdtpcl, scaledivcd, empnum, frgivs_crp_yn, ltgmktdivcd, etb_date, fadivcd, vtr_epr_yn, fundco_yn` | 기업 마스터 — 시드/프로필 |
| `public.origin_kis_ra__s_ra603` | `bse_yr, upchecd, tseximdivcd, tscdcg, tscdvl, tstrdwgt` | HS × 산업분류(MTI) 비중 메타 |
| `public.edge` | `from_bizno → to_bizno, trade_year, sly_amt` | 기업 간 거래 (방향성) |

**운영 31 public 테이블 무수정** — SELECT 만, INSERT/UPDATE/DDL 없음.

## 응답 표준

```http
Content-Type: application/json; charset=utf-8
```

에러 응답은 FastAPI 기본 `{"detail": "..."}` 형식:

```json
{ "detail": "db unreachable: OperationalError" }
```

## 엔드포인트 요약

| Method | Path | 용도 | 비용 |
|---|---|---|---|
| `POST` | `/api/shock/fetch_subgraph` | HS → 시드 → N차 확장 그래프 조회 | 시드 + hop SQL × N |
| `POST` | `/api/shock/propagate` | 거듭제곱급수 합 쇼크 전파 | round × active fanout |
| `POST` | `/api/shock/extract_first_target` | LLM 분류 → 1차 충격 대상 선정 | 노드별 LLM 호출 1회 |

## 호출 패턴 (UI 분리)

**모듈 1, 3, 2 를 따로 UI 에서 호출** — 통합 chain endpoint (`/api/shock/run_all`)
는 **의도적으로 두지 않음**. 이유:

- 모듈 3 의 노드별 LLM 호출이 CPU 추론 시 노드 100 개 = 분 단위 가능
- 사용자에게 *단계별 진행 상태* 를 보여주는 게 UX 정합
- 클라이언트가 각 단계 결과를 확인/조정 후 다음 단계 진행 가능

**시드 N 개를 모듈 2 에 한 번에 넘기는 패턴** — `propagate` 는 init 에 *선형*
이라 N 개 시드를 `init_sub_graph` 의 key 로 모두 묶어 *단일 호출* 권장.
실측 N=500 까지 wall time 1.6 배 이내. N 번 개별 호출은 동일 결과를 N 배 시간
으로 계산하는 낭비.

```python
# ✅ 권장
primary = extract_first_target(node_list=[...], hscode=...)
init = {b: 1.0 for b in primary}                  # 균등 충격이 default
result = propagate_shock(edges=..., init_sub_graph=init)

# ❌ 비효율 — 같은 결과를 N 배 시간으로
results = [propagate_shock(edges=..., init_sub_graph={b: 1.0}) for b in primary]
```

---

## `POST /api/shock/fetch_subgraph`

HS → 시드 → N 차 확장 그래프 조회.

### Request

```json
{
  "hscode": "3801300000",
  "n_of_child": 3,
  "mode": "BFS"
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `hscode` | string(4~10) | ★ | HS 6자리 또는 10자리. 10자리면 앞 6자리 사용. |
| `n_of_child` | int(1~6) |   | N 차 확장 깊이 (default 3). |
| `mode` | `"BFS"` \| `"DFS"` |   | 확장 방식. 결과 set 동일, 알고리즘만 다름 (default `BFS`). |

### Response 200

```json
{
  "nodes": [
    {"bizno": "1018116406", "upchecd": "380130"},
    {"bizno": "1130452404", "upchecd": "CW2610"}
  ],
  "edges": [
    {
      "from_bizno": "1018116406",
      "to_bizno":   "1130452404",
      "years_rate": {"2024": 0.6, "2025": 0.4},
      "all_rate":   0.12
    }
  ]
}
```

### 시드 추출 SQL

```sql
SELECT DISTINCT em.bizno, em.upchecd
FROM public.origin_kis_em__s_em001 em
WHERE em.upchecd = :hs6
  AND EXISTS (
      SELECT 1 FROM public.origin_kis_ra__s_ra603 ra
      WHERE ra.upchecd = em.upchecd
  )
```

### 확장 알고리즘

- **`BFS`** — hop-by-hop SQL. 각 hop 마다 frontier 의 in/out edge SELECT.
- **`DFS`** — iterative stack. 같은 depth 한도, 결과 set 은 BFS 와 동일.

응답에 visit-order 가 들어가지 않으므로 두 모드의 *결과 node/edge set 은 항상
동일*. 차이는 알고리즘 trace 뿐.

### 비율 정의

| 필드 | 분모 | 의미 |
|---|---|---|
| `all_rate` | `SUM(sly_amt(from→*))` (전 연도 합) | source 의 outgoing 행 정규화 (Σ_out = 1). 쇼크 전파의 weight 으로 직결. |
| `years_rate[yr]` | `SUM(sly_amt(from→*, yr))` | source 의 연도별 outgoing 중 비중 (yr 별 Σ_out = 1). |

`all_rate` 의 Σ_out = 1 보장은 모듈 2 의 spectral radius ρ(R) ≤ 1 조건 — 절대 수렴.

---

## `POST /api/shock/propagate`

거듭제곱급수 합 쇼크 전파.

### Request

```json
{
  "edges": [
    {"from_bizno": "A", "to_bizno": "B", "rate": 0.3},
    {"from_bizno": "A", "to_bizno": "D", "rate": 0.2}
  ],
  "init_sub_graph": {"A": 100.0}
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `edges` | list of `{from_bizno, to_bizno, rate}` | ★ | propagation weight. 보통 `fetch_subgraph.edges[i].all_rate` 를 그대로 매핑. |
| `init_sub_graph` | `{bizno: float}` | ★ | 초기 충격. N 개 시드를 한 번에 묶어 전달 권장. |

### Response 200

```json
{
  "shock_list": [
    {"bizno": "A", "shock": 107.758621},
    {"bizno": "B", "shock":  38.793103},
    {"bizno": "C", "shock":  19.396552},
    {"bizno": "D", "shock":  21.551724},
    {"bizno": "E", "shock":   8.620690}
  ],
  "total_shock": 196.120690,
  "iterations": 19,
  "converged": true
}
```

| 필드 | 의미 |
|---|---|
| `shock_list[i].shock` | 해당 노드의 누적 파급 (`Σ_k R^k @ init` 의 i 번째 원소). |
| `total_shock` | `Σ shock_list`. |
| `iterations` | 실제 진행한 round 수. |
| `converged` | `true` = epsilon 컷오프로 자연 종료. `false` = `max_iter=500` 도달 (ρ(R) ≥ 1 의심). |

### 알고리즘

```
total_effect = init
current_shock = init
while True:
    next_shock = {}
    for (src, tgt, rate) in edges:
        if src in current_shock:
            propagated = current_shock[src] * rate
            if abs(propagated) > epsilon:
                next_shock[tgt] += propagated
    if not next_shock: break
    total_effect += next_shock
    current_shock = next_shock
```

내부 구현은 `out_by_src` dict 로 source 인덱싱 → round 당 *active source* 의
fanout 만 순회 (O(\|active\| · avg_fanout)). default `epsilon = 1e-8`,
`max_iter = 500`.

### 선형성 & 시드 수 영향

| 시드 수 | iterations | wall (2k 노드, 10k edges) | total_shock |
|---|---|---|---|
| 1 | 41 | 99 ms | 332 |
| 5 | 45 | 112 ms | 1,654 |
| 20 | 49 | 138 ms | 6,291 |
| 100 | 54 | 146 ms | 32,348 |
| 500 | 58 | 161 ms | 163,983 |

시드 500 배 증가에 wall time 1.6 배 — *시드 수와 비용은 거의 무관*. `total_shock`
만 시드 수에 정확히 선형 (선형성 증명).

---

## `POST /api/shock/extract_first_target`

LLM 분류 → 1차 충격 대상 기업 선정. `node_list` 의 각 bizno 를 LLM 으로
`HIGH / MEDIUM / LOW / NONE` 분류하고 `HIGH + MEDIUM` 만 반환.

### Request

```json
{
  "node_list": ["1018116406", "1130452404"],
  "hscode": "3801300000",
  "trade_year": "2024"
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `node_list` | list of bizno string | ★ | 후보 기업 (fetch_subgraph 결과 nodes 의 bizno). |
| `hscode` | string(4~10) | ★ | 충격 원인 HS6/HS10. LLM 비교 기준. |
| `trade_year` | string \| null |   | s_ra603 메타 조회 연도. `null` 이면 메타 skip (system prompt ~50-80 토큰 절감). |

### Response 200

```json
{
  "node_list": ["1018116406"]
}
```

`HIGH` + `MEDIUM` 으로 분류된 bizno 만. 분류 실패 (LLM 파싱 실패, 재시도 1회
포함) 는 `NONE` 으로 fallback 되어 제외.

### LLM 분류 기준

| 카테고리 | 의미 |
|---|---|
| `HIGH` | 충격 HS 가 매출 또는 원가에 *직접 30% 이상* 영향. |
| `MEDIUM` | 10~30% 또는 원자재/공급사슬 경로의 *강한 간접* 영향. |
| `LOW` | < 10%. |
| `NONE` | 사업과 무관. |

### 기업 프로필 컬럼 (Plan B — 풍부, 10 컬럼)

LLM user prompt 에 노드별로 차등 입력되는 컬럼:

| 컬럼 | 라벨 | 매핑 |
|---|---|---|
| `mainpdtpcl` | 주요품목 | raw (한국어 텍스트) |
| `scaledivcd` | 규모구분 | `0`=미대상, `1`=대기업, `2`=중소기업, `3`=중견기업 |
| `empnum` | 종업원수 | raw int |
| `frgivs_crp_yn` | 외국인투자 | `Y`=예, `N`=아니오 |
| `ltgmktdivcd` | 상장시장 | `1`=코스피, `2`=코스닥, 그 외=기타 |
| `upchecd` | 등록HS6 | raw — *충격 HS 와 같으면 즉시 HIGH 후보* |
| `etb_date` | 설립일 | raw (YYYYMMDD) |
| `fadivcd` | 재무업종 | **운영자 미확인** — raw 통과 |
| `vtr_epr_yn` | 벤처기업 | `Y`=예, `N`=아니오 |
| `fundco_yn` | 펀드회사 | `Y`=예, `N`=아니오 |

- `scaledivcd` 의 *2=중소·3=중견* 순서는 사용자 명시 그대로. 운영 데이터와 어긋
  나면 `_CODE_MAPS["scaledivcd"]` 두 라벨 swap.
- `fadivcd` 의미를 알게 되면 `_CODE_MAPS["fadivcd"] = {...}` 한 줄 추가로 즉시 활성.

### System prompt 구조

```
당신은 한국 무역 공급망 분석가입니다.
충격 시나리오: HS=<hs6> 의 외생 수출입 가격/공급 충격이 발생했습니다.
이 HS 의 산업분류/방향 비중 top10: <ra603 메타>  ← trade_year 있을 때만
분류 정의: HIGH = ...  MEDIUM = ...  LOW = ...  NONE = ...
```

### 다운그레이드 (B → A / C 등)

`src/nice_graph/shock/target.py` 모듈 docstring 상단 *"다운그레이드 가이드"*
참조 — 한 줄 수정으로 Plan A (필수 6) 또는 Plan C (최소 2) 로 축소 가능.

| Plan | 컬럼 수 | 노드당 prompt | 100 노드 CPU 추론 |
|---|---|---|---|
| B (현재) | 10 | ~400 토큰 | 100~200 초 |
| A (필수만) | 6 | ~350 토큰 | 80~160 초 |
| C (최소) | 2 | ~200 토큰 | 40~80 초 |

---

## End-to-end 호출 예 (curl)

```bash
# 1) 그래프 조회
curl -X POST http://localhost:18001/api/shock/fetch_subgraph \
  -H 'Content-Type: application/json' \
  -d '{"hscode":"3801300000","n_of_child":3,"mode":"BFS"}'

# 2) 1차 충격 대상 선정 (응답의 nodes[].bizno 사용)
curl -X POST http://localhost:18001/api/shock/extract_first_target \
  -H 'Content-Type: application/json' \
  -d '{"node_list":["1018116406"],"hscode":"3801300000","trade_year":"2024"}'

# 3) 쇼크 전파 (1+2 의 결과 합성)
curl -X POST http://localhost:18001/api/shock/propagate \
  -H 'Content-Type: application/json' \
  -d '{
    "edges":[{"from_bizno":"1018116406","to_bizno":"X","rate":0.5}],
    "init_sub_graph":{"1018116406":1.0}
  }'
```

## 시나리오 래퍼 — 파급 알고리즘 아규먼트

`src/nice_graph/shock/scenario.py` 의 두 시나리오(`run_tariff_shock` =
관세 충격, `run_transaction_change` = 거래 변화)가 파급 파이프라인에 투입하는
아규먼트 전체 목록. 파이프라인 3계층:

```
시나리오 래퍼 → assemble_propagation_input (R 행렬 + init 벡터 조립)
            → propagate_shock (거듭제곱급수 합 엔진)
```

아규먼트의 작용 지점이 갈린다: 대부분은 **조립 단계에서 R 행렬을 빚고**,
`seed_shock` 은 **init 벡터**, `epsilon`/`max_iter` 만 **순수 엔진 종료조건**.

### 공통 아규먼트 (관세 충격 · 거래 변화 둘 다)

| 아규먼트 | 기본값 | 역할 | 알고리즘 영향 |
|---|---|---|---|
| `seeds` | (필수) | 1차 기업 `(bizno, upchecd)` | init 벡터의 비영 위치 |
| `directions` | `("upstream","downstream")` | 계산 방향 집합 | 방향마다 전파 1세트씩 독립 산출 |
| `weight_a` | `1.0` | 하류=매출 파급(downstream) 비중 | downstream rate에 곱 → R 감쇠 |
| `weight_b` | `1.0` | 상류=매입 파급(upstream) 비중 | upstream rate에 곱 → R 감쇠 |
| `depth` | `3` | 서브그래프 추출 hop | R 행렬 크기(노드 범위) 결정 |
| `trade_year` | `None`(전체) | `company_edge.trade_year` 연단위 필터 | edge 집합 → R 내용 변경 |
| `within_subgraph` | `True` | 서브그래프 내부 엣지로 한정 | R 경계(누수 엣지 차단) |
| `damping` | `0.85` | 감쇠계수 | rate 정규화 분모 스케일 |
| `seed_shock` | `1.0` | 시드 초기 충격량 | init 벡터 값 |
| `normalize` | `"source"` | rate 분모 기준: `source`(Σ_out≤1 수렴보장) / `counterparty`(Σ_in 비중) | R 정규화 파티션 |

`weight_a`↔downstream, `weight_b`↔upstream 바인딩은 `_weight_for()` 한 곳에서
결정: `return weight_a if direction == "downstream" else weight_b`.

### 관세 충격 (`run_tariff_shock`) — 전용

- `edge_overrides` **없음**(`None` 고정) → W 불변, init 벡터만 주입
- 전파 횟수: 방향당 **1회**
- 결과: 방향별 절대 shock 벡터

### 거래 변화 (`run_transaction_change`) — 전용

| 아규먼트 | 기본값 | 역할 |
|---|---|---|
| `edge_overrides` | **(필수, 비면 ValueError)** | `{(from_bizno, to_bizno): g}` — 저장방향(셀러→바이어) 키, g∈[0,1] |

- 전파 횟수: 방향당 **2회** — baseline(원 W) + changed(수정 W)
- 변화분: `Δ = changed − baseline` (difference-of-runs)
- 최적화: changed 는 in-memory `_apply_overrides` 로 g 반영(2차 DB 조립 생략)

### 엔진 레벨 (`propagate_shock`) — `**propagate_kwargs` 로 전달

| 아규먼트 | 기본값 | 역할 |
|---|---|---|
| `epsilon` | `1e-8` | round 내 모든 \|propagated\| ≤ epsilon 이면 자연 수렴 종료 |
| `max_iter` | `500` | 무한루프 방지 상한 (도달 시 `converged=False`) |

### 랜덤 g 생성 (`RandomOverrideSpec`) — 거래 변화의 `edge_overrides` 공급원

| 필드 | 기본값 | 역할 |
|---|---|---|
| `side` | `"both"` | `sales`(1차→2차 매출) / `purchase`(2차→1차 매입) / `both` |
| `low` / `high` | `0.0` / `1.0` | g 난수 범위 (상한 1 → 수렴 보장) |
| `seed` | `None` | 재현용 난수 시드 |
| `only_firms` | `None` | 일부 1차 기업 bizno 한정 (None=연계된 전체) |

> 두 시나리오의 본질적 차이는 단 하나: 관세 충격은 **R 고정 + init 주입(1회 전파)**,
> 거래 변화는 **R 수정 + 차분(2회 전파)**. 나머지 공통 아규먼트는 동일하게 흐른다.

---

## 구현 파일 위치

| 모듈 | 파일 |
|---|---|
| `fetch_subgraph` | `src/nice_graph/shock/fetch.py` |
| 시나리오 래퍼 | `src/nice_graph/shock/scenario.py` |
| `propagate_shock` | `src/nice_graph/shock/propagate.py` |
| `extract_first_target` | `src/nice_graph/shock/target.py` |
| 라우터 | `src/nice_graph/api/routers/shock.py` |
| LLM 클라이언트 | `src/nice_llm/client.py` (`LlmJsonClient.classify_choice`) |

운영 코드 수정 이력 / 다운그레이드 / 매핑 추가 방법은 각 파일의 docstring
상단 가이드 블록 참조.
