# edge_aggregate — 분기 신고 → 연단위 거래 엣지 집계

> 모듈: `src/nice_graph/shock/edge_aggregate.py`
> 역할: 분기 부가세 신고 원천을 전파 그래프용 **연단위 거래 엣지**로 합치고
> **거래 비중(rate)** 을 재계산. **DB 무변경(read-only, SELECT만)**.

---

## 1. 왜 필요한가

운영 거래 원천 `public.origin_itg_vat_dat` 은 **분기 부가세 신고 단위**다. 한 거래쌍
(공급기업→거래상대)이 한 해에 **여러 분기 × 여러 신고차수** 행으로 흩어져 있다.

```
같은 1018116406 → 1011312172, 2024년:
  [20240101~20240331] 차수1  180,000
  [20240401~20240630] 차수1  620,000
  [20240701~20240930] 차수1  ...
  ...
```

전파 그래프는 `(from, to, 연도)` 당 **엣지 1개 + 비중**을 요구한다 → 분기·차수 행을
연단위로 **롤업**하고, 합쳐진 금액 기준으로 각 기업의 **거래처 비중을 다시 계산**해야 한다.
이 모듈이 그 변환을 **메모리 계산으로만** 수행한다(테이블 생성/적재 없음).

---

## 2. 원천 컬럼 매핑

| 원천 `origin_itg_vat_dat` | 산출 | 비고 |
|---|---|---|
| `bizno` | `from_bizno` | 공급기업 (Edge 방향 = 공급→구매, NICE 기준) |
| `trs_obj_bizrregno` | `to_bizno` | 거래상대 |
| `vat_stmt_yr` | `trade_year` | 신고연도 = 집계 단위 |
| `slyvl` | `sly_amt` | 공급가액(합산 대상) |
| `ttn_prid_st_date`·`end_date` | — | 분기 기간(연단위로 롤업) |
| `vat_phs_rnu_divcd` | — | 신고차수 `1`=예정 / `2`=수정 |

---

## 3. 작업 흐름

```
origin_itg_vat_dat (분기·차수 행)
        │  ① base CTE: TRIM 정리 + 필요 컬럼 추출 (+ 연도/기업 필터)
        ▼
   행 단위 정규화
        │  ② rows CTE: 신고차수 정책(REPLACE/SUM_ALL/FIRST_ONLY)으로 행 선택
        ▼
   연단위 집계
        │  ③ GROUP BY (from,to,year): SUM(slyvl)=sly_amt, COUNT=n_filings
        ▼
   YearlyEdge 리스트
        │  ④ normalize=True 면: rate = sly_amt / Σ_out(from,year)
        ▼
   list[YearlyEdge]
```

### ① base CTE — 원천 정리
`TRIM` 으로 문자 컬럼 공백 제거, `slyvl` 결측은 0. `trade_year`/`only_biznos` 필터를
여기서 WHERE 로 적용(없으면 전체).

### ② rows CTE — 신고차수 정책 (핵심)
정책에 따라 **어떤 행을 살릴지**가 달라진다.

| 정책 | rows CTE 동작 | 의미 |
|---|---|---|
| `REPLACE` (기본) | `(from,to,연도,분기)` 그룹의 **최대 차수 행만** 남김 (`MAX(chasu) OVER (PARTITION …)`) | 같은 기업쌍·분기에 2차(수정)가 있으면 1차 폐기·2차 채택 |
| `SUM_ALL` | 전 행 그대로 | 차수 무관 단순 합산 |
| `FIRST_ONLY` | `chasu='1'` 만 | 예정신고만, 수정 무시 |

> `REPLACE` 는 **분기 단위 '대체'** 이지 행 단위 중복제거가 아니다. 같은 분기·같은 차수의
> 복수 세금계산서는 그대로 합산된다(완전중복 제거는 NICE Raw 단계 책임).

### ③ 연단위 집계
`GROUP BY (from, to, year)` 로 분기 행을 합쳐 `sly_amt = SUM(slyvl)`,
`n_filings = COUNT(*)`(집계에 들어간 신고 행 수). `HAVING SUM > 0` 으로 0원 엣지 제거.

### ④ 거래 비중 재계산 (`normalize=True`, 선택)
파이썬에서 각 `(from_bizno, trade_year)` 의 outgoing 합을 분모로:
```
rate = sly_amt / Σ_out(from, year)
```
연·기업 묶음마다 분모를 다시 잡으므로 같은 `(from, year)` 의 **비중 합 = 1.0**.
(이건 결과집합 전체 기준 **글로벌 거래처 비중**. 서브그래프 한정 정규화는 `assemble` 이 별도 수행.)

---

## 4. API

```python
from nice_graph.shock.edge_aggregate import aggregate_yearly_edges, AmendmentPolicy

edges = aggregate_yearly_edges(
    policy=AmendmentPolicy.REPLACE,   # 신고차수 정책
    trade_year=None,                  # 연도 필터 (None=전 연도 각각)
    only_biznos=None,                 # 공급기업(from) 한정
    normalize=False,                  # rate 채움 여부
    engine=None,                      # Engine 주입(테스트 격리), None=운영 PG
)   # -> list[YearlyEdge]
```

### `YearlyEdge`
| 필드 | 의미 |
|---|---|
| `from_bizno` / `to_bizno` | 공급→구매 |
| `trade_year` | 연도 |
| `sly_amt` | 연 공급가액 합 |
| `n_filings` | 집계에 포함된 분기 신고 행 수 |
| `rate` | source 정규화 비중 (`normalize=True` 시, 아니면 `None`) |

`(from_bizno, to_bizno, trade_year)` 유일.

---

## 5. 실데이터 동작 (현재 PoC 데이터)

| 정책 | 엣지 수 | Σ sly_amt | Σ n_filings |
|---|---|---|---|
| `REPLACE` | 6,287 | 43,470,441,546 | 10,103 |
| `SUM_ALL` | 6,287 | 43,637,584,273 | 10,114 |
| `FIRST_ONLY` | 6,085 | 42,299,290,273 | 9,851 |

- `REPLACE` vs `SUM_ALL`: 공존 1차 **11행**(10,114−10,103)을 정확히 제외 → 10개 엣지 금액 감소.
- `normalize`: 모든 `(from, year)` 비중 합 = **1.0** 검증.

> ⚠️ **현재 원천은 신고기업(bizno)이 단 1개**(샘플)라 모든 엣지의 from 이 동일하다.
> 모듈 로직은 일반적이며, NICE 전체 거래 데이터가 적재되면 그대로 전체 그래프를 만든다.

---

## 6. 범위 밖 (현재 미포함)

- **본점(RPS_BIZNO) 지점→대표기업 롤업** — 원천에 `RPS_BIZNO` 컬럼이 없음(별도 매핑 필요).
- **매출/매입 양측 신고 View 교차 중복제거** — 원천이 단일 View 라 해당 없음.

---

## 7. 테스트
`tests/test_edge_aggregate.py` (6건). DB 의존을 피하려 **SQLite in-memory** 에
`ATTACH DATABASE ':memory:' AS public` 으로 스키마를 흉내내고 `engine` 주입으로 격리.
정책 3종·정규화(비중 합=1)·연도 필터 검증. SQL 은 `CAST(… AS FLOAT)` 로 PG/SQLite 양립.
