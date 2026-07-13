# 런북 — `company_edge.trade_rate` / `sell_rate` / `buy_rate` 갱신 (nice_migrate)

> **언제**: `company_edge` 데이터를 새로 적재했거나 rate/share 컬럼이 비었/초기화됐을 때.
> **무엇**: 각 거래의 `trade_rate` + 거래망 공유율 `sell_rate`·`buy_rate` 를 **연도별 정규화**로 채운다.
> **왜**: `trade_rate` 는 shock 전파 입력 가중치, `sell_rate`/`buy_rate` 는 CRI 입력(`sell_share`/`buy_share`).
>   비어 있으면 관련 API 가 정상 동작 안 함. (한 번의 실행으로 세 컬럼 모두 갱신)

계산식:
```
trade_rate(from→to, year) = sly_amt / Σ_out(from, year)     # source(셀러) 정규화
sell_rate  (=sell_share)  = sly_amt / Σ_out(source=from)    # trade_rate 와 동일 공식·값
buy_rate   (=buy_share)   = sly_amt / Σ_out(target=to)      # 바이어 매출로 정규화
```
- `trade_rate`·`sell_rate`: 같은 (셀러 from, 연도) 합 = 1 (source 정규화). ρ(전파행렬)≤1 로 수렴.
- `buy_rate`: 바이어의 **매출**(Σ_out(to))로 나눔 → **상한 1 아님**(자기 매출보다 많이 사면 >1).
  바이어가 판매 이력이 없으면(Σ_out(to)=0) `buy_rate=0`.

---

## 0. 사전 조건

- 운영 PostgreSQL 접속 정보 (host/port/user/password/dbname) 또는 DSN.
- `nice_migrate` 실행 환경 = **`src` 가 `PYTHONPATH` 에 있는 파이썬**(sqlalchemy·psycopg 포함).
  - 가장 쉬운 건 이미 DB env 가 세팅된 **컨테이너 안에서** 실행(아래 방법 A).

---

## 1. 실행 절차 (반드시 dry-run → 실행 → 검증 순서)

### ① dry-run (변경 없이 대상 행 수만 확인)
```bash
python -m nice_migrate --dry-run
```
출력 예: `{"target_rows": 754, "updated": 0, "dry_run": true}`

### ② 실제 실행 (전 연도)
```bash
python -m nice_migrate
```
출력 예:
```json
{"target_rows": 754, "updated": 754,
 "rate_sum_min": 1.0, "rate_sum_max": 1.0, "rate_sum_avg": 1.0,
 "shares_updated": 754, "buy_rate_updated": 380,
 "sell_vs_rate_diff": 0.0, "buy_rate_min": 0.0, "buy_rate_max": 13.05,
 "buy_rate_avg": 0.26, "buy_rate_null": 0}
```
→ `rate_sum_*`≈1.0 (셀러 정규화), `sell_vs_rate_diff`≈0 (sell_rate=trade_rate),
  `buy_rate_null`=0 이면 정상. (`--no-shares` 로 공유율 갱신 생략 가능)

### ③ 검증 (아래 §4 SQL)

---

## 2. 실행 방법 (환경별 — 택1)

### 방법 A) 컨테이너 안에서 (권장 — DB env 이미 세팅됨)
demo/graph-analysis 등 `POSTGRES_*` env 가 있는 컨테이너를 이용:
```bash
docker exec -e PYTHONPATH=/app/src <컨테이너명> python -m nice_migrate --dry-run
docker exec -e PYTHONPATH=/app/src <컨테이너명> python -m nice_migrate
```
(예: `<컨테이너명>` = `nice-demo`)

### 방법 B) 호스트에서 환경변수로
```bash
cd <repo>
POSTGRES_HOST=172.30.1.101 POSTGRES_PORT=5432 \
POSTGRES_USER=nice POSTGRES_PASSWORD=<pw> POSTGRES_DB=nice_innovation \
PYTHONPATH=src python -m nice_migrate --dry-run
# 확인 후 --dry-run 빼고 재실행
```

### 방법 C) DSN 직접
```bash
PYTHONPATH=src python -m nice_migrate \
  --dsn "postgresql+psycopg://nice:<pw>@172.30.1.101:5432/nice_innovation"
```

### 방법 D) .env 파일 사용
```bash
PYTHONPATH=src python -m nice_migrate --env-file .env
```

---

## 3. 옵션 레퍼런스

| 옵션 | 설명 |
|---|---|
| `--dry-run` | 갱신 없이 대상 행 수만 출력 (먼저 항상 실행 권장) |
| `--year 2026` | 특정 거래연도만 갱신. **미지정 시 전 연도** |
| `--dsn <DSN>` | 전체 DSN 지정 (주면 개별 접속인자 무시) |
| `--host/--port/--user/--password/--dbname` | 개별 접속 인자 |
| `--env-file <경로>` | `.env`(KEY=VALUE) 주입 (기존 환경변수 우선) |
| `--schema public` | 대상 스키마 (기본 public) |
| `--no-alter` | `trade_rate` 컬럼 타입 자동보정(double precision) **비활성** |
| `--no-shares` | `sell_rate`/`buy_rate`(거래망 공유율) 동시 갱신 **비활성** (기본은 함께 채움) |
| `-v`, `--verbose` | 상세 로그 |

> **자동 컬럼 보정**: `trade_rate` 가 비율(0~1)을 못 담는 타입(예 NUMERIC(3,6))이면 자동으로
> `double precision` 으로 `ALTER` 한다. 막으려면 `--no-alter`. (이미 double precision 이면 무동작.)

---

## 4. 검증 SQL

```sql
-- (1) 전부 채워졌나 (세 컬럼 NULL 0 이어야)
SELECT COUNT(*) AS 전체,
       COUNT(*) FILTER (WHERE trade_rate IS NULL) AS trade_NULL,
       COUNT(*) FILTER (WHERE sell_rate  IS NULL) AS sell_NULL,
       COUNT(*) FILTER (WHERE buy_rate   IS NULL) AS buy_NULL
FROM public.company_edge;

-- (2) source 정규화 검산 — 연도별 (셀러 from) 합 = 1 이어야 (trade_rate·sell_rate 공통)
SELECT trade_year, ROUND(MIN(s)::numeric,4) AS min_합, ROUND(MAX(s)::numeric,4) AS max_합
FROM (SELECT trade_year, from_bizno, SUM(trade_rate) s
      FROM public.company_edge GROUP BY 1,2) x
GROUP BY 1 ORDER BY 1;
-- 결과의 min·max 가 모두 1.0000 이면 정상.

-- (3) sell_rate = trade_rate (동일 공식) 확인 → 0 이어야
SELECT MAX(ABS(sell_rate - trade_rate)) AS 최대차 FROM public.company_edge;

-- (4) buy_rate 점검 — 바이어 매출 정규화라 상한 1 아님, 무매출 바이어는 0
SELECT MIN(buy_rate), MAX(buy_rate), ROUND(AVG(buy_rate)::numeric,4) AS 평균,
       COUNT(*) FILTER (WHERE buy_rate = 0) AS 무매출바이어수
FROM public.company_edge;
```

---

## 5. 실제 실행 예

### 5-A. trade_rate 초기화 복구 (2026-07-01)
```
상태: 754행 전부 trade_rate NULL (초기화됨). 연도 2024(310)·2026(444).
$ docker exec -e PYTHONPATH=/app/src nice-demo python -m nice_migrate
  {"target_rows": 754, "updated": 754, "rate_sum_min/max/avg": 1.0}
검증: NULL 0 / Σ_out(2024·2026 각각) min=max=1.0000 ✅
샘플(포스코 2026 매출): →합동 0.6068, →현대모비스 0.3334, →지오 0.0599 (합=1.0)
```

### 5-B. sell_rate/buy_rate(공유율) 최초 채움 (2026-07-13)
```
상태: sell_rate·buy_rate 컬럼 존재하나 754행 전부 NULL.
$ PYTHONPATH=src python -m nice_migrate
  {"target_rows": 754, "updated": 754, "rate_sum_min/max/avg": 1.0,
   "shares_updated": 754, "buy_rate_updated": 380,
   "sell_vs_rate_diff": 0.0, "buy_rate_min": 0.0, "buy_rate_max": 13.05,
   "buy_rate_avg": 0.26, "buy_rate_null": 0}
검증: sell/buy 754/754 채움, buy>0 380행(나머지 374 무매출바이어=0),
      sell_rate=trade_rate(차 0.0), buy_rate 상한 없음(max 13.05) ✅
```

---

## 6. 주의사항

- **운영 PG 에 UPDATE** 하는 작업 → 반드시 `--dry-run` 먼저.
- **연도별로 따로 정규화**된다 (2024·2026 각각 셀러 합=1). 전 연도 한 번에 돌려도 됨.
- `sly_amt=0` 이거나 셀러의 연도 Σ_out=0 이면 그 rate=0 (분모 0 방지 처리됨).
- **`buy_rate` 는 상한 1 이 아니다** — 바이어 매출(Σ_out(to))로 나누므로 자기 매출보다 많이
  구매하면 1 을 넘는다(정상). 판매 이력 없는 바이어는 `buy_rate=0`.
- 세 컬럼이 **한 트랜잭션**에서 갱신된다(부분 반영 없음). `--no-shares` 로 공유율만 건너뛸 수 있음.
- compose 서비스가 **아니다** — 상시 가동 X, 데이터 적재 후 **1회성 CLI/배치**로 실행.
- 관련: [`README.md`](../README.md) §1-A(nice_migrate), [`CLAUDE.md`](../CLAUDE.md).

---

기호: Σ_out(X)=연도별 X 의 총매출(from=X 기준 sly_amt 합), ρ=rho(전파행렬 spectral radius)
