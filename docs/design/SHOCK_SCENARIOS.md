# 외생충격 시나리오 — 동작 정리

> 대상: `nice_graph.shock` 의 두 시나리오 래퍼(관세 충격 · 거래량 변동).
> 단일 전파 엔진(`propagate_shock`, 거듭제곱급수 합 `Σ_k Rᵏ·init`) 위에 얇은 래퍼 2겹.
> 엔드포인트: `POST /api/shock/scenario` · 라이브러리: `nice_graph.shock.scenario`.

---

## 0. 공통 동작 (두 시나리오 공유)

```
1차 기업(시드) ──▶ depth-3 거래확장(재귀 CTE 유도 부분그래프) ──▶ 방향별 전파 ──▶ 결과
   seeds          assemble_propagation_input            downstream/upstream 각각
```

- **입력 시드** = HS코드로 선별된 1차 기업 `(bizno, upchecd)` 목록.
- **그래프** = 시드에서 **depth 3**까지 확장한 거래 서브그래프. 두 시나리오 모두 이 그래프를 공유.
- **방향(directions)** = `downstream`·`upstream` 중 요청한 것마다 **독립 전파 1세트**씩 산출(기본 둘 다).
- **수렴** = edge `rate` 가 source 기준 정규화(Σ_out≤1) + 감쇠 `damping α<1` → `ρ(R)≤α<1` 절대수렴.

### 방향 ↔ 라벨 규약 (화면기획안 v1.0 확정)

| 방향 | 엣지 진행 | 파급 라벨 | 경제적 의미 | 가중치 | 정규화 분모 |
|---|---|---|---|---|---|
| `downstream` | 셀러→바이어 | **매출 파급** | 1차 기업의 **매출처(고객)** 방향 = 하류 | A (`weight_a`) | 셀러 총매출 |
| `upstream` | 바이어→셀러 | **매입 파급** | 1차 기업의 **매입처(공급사)** 방향 = 상류 | B (`weight_b`) | 바이어 총매입 |

> 방향을 뒤집으면 엣지 방향과 정규화 분모(PARTITION)가 함께 전환되어 Σ_out≤1 수렴이 유지된다.

---

## 1. 관세 충격 (`tariff`)

### 정의
거래 구조(W)는 **그대로 두고**, 1차 기업(시드)에 **외생 충격만 주입**해 파급을 본다.
관세 인상처럼 "특정 기업군이 받는 외부 충격이 거래망을 타고 어디까지 퍼지는가".

### 동작
1. 시드 → depth-3 그래프 조립 (W 불변).
2. 시드 노드의 `init` 벡터에 초기 충격(`seed_shock`) 주입.
3. 요청 방향마다 `Σ_k Rᵏ·init` 누적 전파 → **방향별 절대 파급량**.
4. 전파 **1회/방향**.

### 입력
| 파라미터 | 의미 |
|---|---|
| `seeds` | 1차 기업 `(bizno, upchecd, shock)` |
| `directions` | `downstream`(매출)·`upstream`(매입) 중 택 |
| `weight_a` / `weight_b` | 매출(하류)/매입(상류) 비중 가중치 |
| `depth` | 확장 깊이(기본 3) |
| `trade_year` | 거래연도 필터(None=전 연도) |
| `normalize` | `source`(수렴보장) / `counterparty`(매출·매입 비중 라벨) |

### 출력
- 방향별 **노드 누적 파급량**(`shock_list`), 총합(`total_shock`), 수렴 여부(`converged`).

### 동작 흐름
```
seeds ─▶ assemble(depth3, W 그대로) ─▶ init=seed_shock 주입
      ─▶ [downstream] 전파 → 매출 파급 결과
      ─▶ [upstream]   전파 → 매입 파급 결과
```

---

## 2. 거래량 변동 (`transaction_change`)

### 정의
특정 **1차→2차 거래의 비중(W)을 수정**하고, 그로 인한 파급의 **변화분(Δ)** 을 본다.
"이 거래쌍의 매출/매입 비중이 g배로 바뀌면, 거래망 전체 파급이 얼마나 달라지는가".

### 동작
1. 시드 → depth-3 그래프 조립 (**나머지 엣지는 원본 W 그대로**).
2. `edge_overrides`(또는 `random_override`)에 지정한 **그 엣지의 rate에만 인자 g(0~1) 곱**.
3. 방향마다 **baseline(원 W) + changed(수정 W)** 두 번 전파.
4. 노드별 **변화분 `Δ = changed − baseline`** 반환 (difference-of-runs).
5. 전파 **2회/방향**.

### 입력 — 두 방식
**(a) 특정 거래쌍 지정** — `edge_overrides`
| 필드 | 의미 |
|---|---|
| `from_bizno` | 저장방향 셀러 |
| `to_bizno` | 저장방향 바이어 |
| `factor` | 그 (셀러→바이어) 비중에 곱할 g (0~1) |

**(b) 1차↔2차 일괄 랜덤** — `random_override` (지정 시 edge_overrides 대체)
| 필드 | 의미 |
|---|---|
| `side` | `sales`(매출=1차 판매→2차) / `purchase`(매입=2차 판매→1차) / `both` |
| `low`·`high` | 랜덤 g 범위(⊆[0,1]) |
| `seed` | 재현용 난수 시드 |
| `only_firms` | 일부 1차 기업 한정(None=연계된 모든 1차) |

그 외 공통 파라미터(`directions`/`weight_a·b`/`depth`/`trade_year`/`normalize`)는 관세 충격과 동일.

### 출력
- 방향별 **노드 변화분 Δ**(`shock_list`; g<1이면 보통 음수=파급 감소).
- `applied_overrides` = 실제 적용된 g(랜덤 생성분 포함) — 재현·표시용.

### 동작 흐름
```
seeds ─▶ assemble(depth3) ─┬─▶ baseline 전파(원 W)
                            └─▶ changed 전파(지정 엣지만 rate×g)
                                 → Δ = changed − baseline (노드별)
```

### 핵심 — "나머지 그래프는 그대로"
오버라이드한 엣지 **외에는 분자(거래액)도 분모(Σ_out)도 불변**. depth-3 그래프 전체를
그대로 재사용하면서 **콕 집은 거래만** 바꿔 그 순효과만 분리해낸다.

> `edge_overrides` 키는 항상 **저장방향(셀러→바이어)** — `directions`(매출/매입 관점)와 직교.
> 1차가 2차에 판매하는 거래면 `from=1차,to=2차`, 1차가 2차에서 매입하는 거래면 `from=2차,to=1차`.

---

## 3. 두 시나리오 비교

| 항목 | 관세 충격 (`tariff`) | 거래량 변동 (`transaction_change`) |
|---|---|---|
| 거래 구조 W | **불변** | 지정 엣지 rate × g (**수정**) |
| 충격 주입 | 시드 init 외생 주입 | 없음(구조 변화 자체가 충격원) |
| 전파 횟수 | 방향당 1회 | 방향당 2회(baseline+changed) |
| 결과 의미 | 절대 파급량 | 변화분 Δ(원본 대비) |
| 전용 입력 | — | `edge_overrides` / `random_override` |
| 공유 | 시드·depth-3 그래프·방향·가중치·정규화·연도 — **동일** |

---

## 4. API 조합

```
① POST /api/shock/select_primary   HS코드 → 1차 시드
②(선택) POST /api/shock/assemble    시드 → depth-3 그래프(바꿀 1→2 엣지 식별용)
③ POST /api/shock/scenario         scenario + (관세=시드만 / 거래변동=edge_overrides|random_override)
```
`/api/shock/scenario` 한 콜이 내부에서 depth-3 조립·전파를 모두 수행한다. 바꿀 2차 bizno를
이미 알면 ②는 생략(①+③ 2-콜). 상세 요청 바디·아규먼트 표는 [`SHOCK_API.md`](SHOCK_API.md) 참조.

---

## 5. 구현 위치
| 요소 | 파일 |
|---|---|
| 시나리오 래퍼 | `src/nice_graph/shock/scenario.py` |
| 그래프 조립 | `src/nice_graph/shock/assemble.py` |
| 전파 엔진 | `src/nice_graph/shock/propagate.py` |
| 라우터(`/api/shock/scenario`) | `src/nice_graph/api/routers/shock.py` |
| Streamlit 데모 | `src/nice_demo/app_shock.py` |
