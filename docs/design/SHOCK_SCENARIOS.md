# 외생충격 시나리오 — 동작 정리

> 대상: `nice_graph.shock` 의 두 시나리오 래퍼(외생충격 tariff · 거래량 변동 volume).
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

## 2. 거래량 변동 (`volume`)

> 2026-06-22: 구 `transaction_change`(W 수정·2회 차분, difference-of-runs)는 **폐기**되고
> 거래량 변동은 `volume`(W 불변·편차 전파·매출/매입 반영) 하나로 일원화됐다.

### 정의
특정 기업/거래의 **매출·매입 거래량이 m배(1=중립) 변할 때**, 연결 기업들의 매출/매입이
얼마나 변하는지 본다. "쿠팡 매출 −20% → 연결 기업 매출 몇 % 변동?" 류.

### 동작 (편차 전파)
1. 시드 → depth-3 그래프 조립 (**W 불변**).
2. 변동을 **편차 δ=m−1** 로 주입 (m=1+증감율: 0.8=−20%, 1.1=+10%).
3. **1회** 전파 후 `shock = 1 + Σ_k Wᵏ·δ` (δ=0 노드는 정확히 1=무변화).
4. 노드별 **조정 매출/매입 = shock × 기준액**, 변동율 = `shock − 1`.
5. 매출 변동→하류(downstream), 매입 변동→상류(upstream).

### 입력 — 3가지 (택1·병용)
**(a) `firm_specs` (권장)** — 1차 기업의 매출/매입 거래량 변동
| 필드 | 의미 |
|---|---|
| `bizno` | 1차(시드) 기업 |
| `side` | `sales`(매출=1차→2차) / `purchase`(매입=2차→1차) |
| `factor` | m=1+증감율 |
| `partner` | 2차 bizno. **None이면 1차의 그 side 모든 거래처** |

**(b) `multipliers`** `{bizno: m}` — 기업 전체 매출/매입 m배 (δ 시드 주입)
**(c) `edge_multipliers`** `[{from,to,factor}]` — 특정 거래만, 상대에 거래 비중 가중

각 거래는 **상대(2차) 노드에 거래 비중 가중** `δ=share·(m−1)` 주입 (매출은 상대 매입 비중,
매입은 상대 매출 비중). 방향은 side로 자동 도출.

### pin_seeds — 시드 되돌이
| | 동작 | 시드값 |
|---|---|---|
| `True`(기본) | 시드 incoming 차단 → 입력값 고정 | 정확히 m |
| `False` | 순환 피드백 허용 | 증폭(일반균형) |

### 출력
- 방향별 노드 `shock`(=1+변동율). 매출/매입에 ×반영해 조정액·변동율 산출.

### 동작 흐름
```
seeds ─▶ assemble(depth3, W 불변) ─▶ δ 주입(firm_specs/multipliers/edge_multipliers)
      ─▶ 1회 전파 ─▶ shock=1+Σ_k Wᵏ·δ ─▶ ×매출/매입
```

> 곱셈 중립(1)을 덧셈 전파(0)로 잇는 편차 변환이 핵심 — `init=1` 직접 전파는 변동 0인데도
> 노드값이 폭증(매출×137)하는 버그라, δ=m−1 주입 후 +1 로 복원한다.

---

## 3. 수렴 조건 (왜 항상 수렴하는가)

### 계산 = 행렬 무한등비급수

전파는 round-by-round 로 거듭제곱급수를 누적한다:

```
total = Σ_k Rᵏ·init = (I − R)⁻¹·init      (Neumann 급수 = Leontief 역행렬)
```

이는 스칼라 무한등비급수 `Σ rᵏ = 1/(1−r)` 의 **행렬 일반화**다. k번째 라운드가
`Rᵏ·init` 항을 더하고, 그 항이 `epsilon`(기본 1e-8) 아래로 떨어지면 꼬리를 잘라
종료한다(`max_iter` 기본 500 도달 시 `converged=False`).

### 수렴 조건은 ρ(R) < 1 — 개별 rate < 1 이 아니다

스칼라는 공비 `|r|<1` 이면 수렴하지만, 여기선 공비가 **행렬 R** 이라 조건이
**스펙트럴 반경 `ρ(R) < 1`** 이다. 개별 엣지 rate 가 모두 <1 이어도 **팬아웃**이 있으면
발산할 수 있다:

> 반례: 노드 A 가 B·C 로 각각 rate 0.8(<1) 을 보내고(Σ_out=1.6), B·C 가 A 로 0.8 씩
> 되돌리면 한 바퀴마다 질량이 증폭 → 발산. 개별 값이 아니라 **나가는 합**이 문제.

충분조건: `ρ(R) ≤ ‖R‖ = max_node Σ_out(node)`. 즉 **각 노드의 나가는 rate 합 < 1**.

### source 정규화 + damping 이 합을 묶는다

`rate = direction_weight · damping · (amt / 분모) · g` 에서 한 source 의 출력 엣지를
다 더하면 `Σ(amt)/분모` 가 1 이하라:

```
Σ_out(node) = direction_weight · damping · (sub_total/분모) · g  ≤  damping (≈0.85) < 1
```

개별 rate 가 아니라 **합이 damping 으로 묶여** `ρ(R) ≤ damping < 1` → 절대수렴.

### 서브그래프 truncation 과 leakage (within_subgraph)

실제 그래프에서 depth-N 서브그래프는 잘린 그래프라, 경계 노드의 거래가 밖으로
나간다. 서브그래프 안 출력 합이 자연히 1 이 된다는 보장은 없다 — 이는 `within_subgraph`
파라미터로 처리한다:

| within_subgraph | 분모 | Σ_out(node) | 의미 |
|---|---|---|---|
| `True` (기본) | 서브그래프 안 합 `sub_total` | **= damping** (정확히) | 닫힌계 가정 — 충격 전량 서브그래프 내 유지 |
| `False` | 실제 전체 합 `full_total` | **≤ damping** (누수만큼 작음) | 밖으로 가는 비중은 leakage 로 소멸, 실제 비중 충실 |

실측(real `company_edge`, depth-2): `True` → 모든 노드 Σ_out=0.8500, `False` → 0.7915~0.8500.
**두 모드 모두 Σ_out ≤ damping < 1** → truncation 이 있어도 수렴은 깨지지 않는다. 누수는
ρ(R) 를 **더 작게** 만들 뿐이다(수렴의 적이 아니라 친구). 트레이드오프는 정확도뿐 —
`True` 는 충격 과대평가(닫힌계), `False` 는 과소평가(누수분 소멸).

### damping=1.0 과 순환

| 그래프 | damping=1.0 | damping<1 |
|---|---|---|
| DAG(순환 없음) | 수렴 (R nilpotent, 경로 유한 → 실질 ρ=0) | 수렴 |
| 순환 포함 | **비수렴** (Σ_out=1, 충격이 루프를 안 줄고 순회 → ρ=1) | 수렴 (ρ≤damping<1) |

순환 그래프에서 damping<1 이 "매 단계 (1−α) 흡수"로 루프를 감쇠시키는 안전장치다.

### 유일한 발산 위험 — normalize=counterparty

`normalize="counterparty"` 는 분모가 source 가 아니라 **거래상대**(매출/매입 비중 라벨)라
한 source 의 Σ_out 이 1 을 넘을 수 있다 → `ρ(R)>1` 가능 → 발산 시 `converged=False` 로
표면화. source 정규화에는 없는 위험이며, 그래서 counterparty 모드는 경고를 띄운다.

---

## 4. 두 시나리오 비교

| 항목 | 외생충격 (`tariff`) | 거래량 변동 (`volume`) |
|---|---|---|
| 거래 구조 W | **불변** | **불변** |
| 주입 | 시드 init 외생 충격(seed_shock) | 편차 δ=m−1 (firm_specs/multipliers/edge_multipliers) |
| 전파 횟수 | 방향당 1회 | 방향당 1회 |
| 결과 의미 | 절대 파급량 | shock=1+δ전파 → ×매출/매입 |
| 전용 입력 | — | `firm_specs`·`multipliers`·`edge_multipliers`·`pin_seeds` |
| 공유 | 시드·depth-3 그래프·방향·가중치·정규화·연도·industry_code — **동일** |

---

## 5. API 조합

```
① POST /api/shock/select_primary   HS코드 → 1차 시드
②(선택) POST /api/shock/assemble    시드 → depth-3 그래프(바꿀 1→2 엣지 식별용)
③ POST /api/shock/scenario         scenario + (tariff=시드 외생충격 / volume=firm_specs·multipliers·edge_multipliers)
```
`/api/shock/scenario` 한 콜이 내부에서 depth-3 조립·전파를 모두 수행한다. 바꿀 2차 bizno를
이미 알면 ②는 생략(①+③ 2-콜). 상세 요청 바디·아규먼트 표는 [`SHOCK_API.md`](SHOCK_API.md) 참조.

---

## 6. 구현 위치
| 요소 | 파일 |
|---|---|
| 시나리오 래퍼 | `src/nice_graph/shock/scenario.py` |
| 그래프 조립 | `src/nice_graph/shock/assemble.py` |
| 전파 엔진 | `src/nice_graph/shock/propagate.py` |
| 라우터(`/api/shock/scenario`) | `src/nice_graph/api/routers/shock.py` |
| Streamlit 데모 | `src/nice_demo/app_shock.py` |
