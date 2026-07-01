# 런북 — `company_edge.trade_rate` 갱신 (nice_migrate)

> **언제**: `company_edge` 데이터를 새로 적재했거나 `trade_rate` 컬럼이 비었/초기화됐을 때.
> **무엇**: 각 거래의 `trade_rate` 를 **연도별 source(셀러) 정규화**로 채운다.
> **왜**: shock 전파의 입력 가중치이자 CRI 의 `sell_share` 원천. 비어 있으면 두 API 가 정상 동작 안 함.

계산식:
```
trade_rate(from→to, year) = sly_amt(from→to, year) / Σ_out(from, year)
```
→ 같은 (셀러 from, 연도)의 `trade_rate` 합 = 1 (source 정규화). ρ(전파행렬)≤1 로 수렴 보장.

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
 "rate_sum_min": 1.0, "rate_sum_max": 1.0, "rate_sum_avg": 1.0}
```
→ `rate_sum_*` 가 **≈1.0** 이면 정상(연도별 셀러 정규화 검산 통과).

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
| `-v`, `--verbose` | 상세 로그 |

> **자동 컬럼 보정**: `trade_rate` 가 비율(0~1)을 못 담는 타입(예 NUMERIC(3,6))이면 자동으로
> `double precision` 으로 `ALTER` 한다. 막으려면 `--no-alter`. (이미 double precision 이면 무동작.)

---

## 4. 검증 SQL

```sql
-- (1) 전부 채워졌나 (NULL 0 이어야)
SELECT COUNT(*) AS 전체, COUNT(trade_rate) AS 값있음,
       COUNT(*) FILTER (WHERE trade_rate IS NULL) AS NULL수,
       MIN(trade_rate), MAX(trade_rate)
FROM public.company_edge;

-- (2) source 정규화 검산 — 연도별 (셀러 from) 합 = 1 이어야
SELECT trade_year, ROUND(MIN(s)::numeric,4) AS min_합, ROUND(MAX(s)::numeric,4) AS max_합
FROM (SELECT trade_year, from_bizno, SUM(trade_rate) s
      FROM public.company_edge GROUP BY 1,2) x
GROUP BY 1 ORDER BY 1;
-- 결과의 min·max 가 모두 1.0000 이면 정상.
```

---

## 5. 실제 실행 예 (2026-07-01, 초기화 복구)

```
상태: 754행 전부 trade_rate NULL (초기화됨). 연도 2024(310)·2026(444).

$ docker exec -e PYTHONPATH=/app/src nice-demo python -m nice_migrate --dry-run
  {"target_rows": 754, "updated": 0, "dry_run": true}

$ docker exec -e PYTHONPATH=/app/src nice-demo python -m nice_migrate
  {"target_rows": 754, "updated": 754,
   "rate_sum_min": 1.0, "rate_sum_max": 1.0, "rate_sum_avg": 1.0}

검증: NULL 0 / Σ_out(2024·2026 각각) min=max=1.0000 ✅
샘플(포스코 2026 매출): →합동 0.6068, →현대모비스 0.3334, →지오 0.0599 (합=1.0)
```

---

## 6. 주의사항

- **운영 PG 에 UPDATE** 하는 작업 → 반드시 `--dry-run` 먼저.
- **연도별로 따로 정규화**된다 (2024·2026 각각 셀러 합=1). 전 연도 한 번에 돌려도 됨.
- `sly_amt=0` 이거나 셀러의 연도 Σ_out=0 이면 그 rate=0 (분모 0 방지 처리됨).
- compose 서비스가 **아니다** — 상시 가동 X, 데이터 적재 후 **1회성 CLI/배치**로 실행.
- 관련: [`README.md`](../README.md) §1-A(nice_migrate), [`CLAUDE.md`](../CLAUDE.md).

---

기호: Σ_out=연도별 셀러 총매출(from 기준 sly_amt 합), ρ=rho(전파행렬 spectral radius)
