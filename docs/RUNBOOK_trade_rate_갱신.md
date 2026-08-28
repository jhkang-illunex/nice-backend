# 런북 — `company_edge` rate/공유율 + `company_credit_cri` cri2 점수 갱신 (nice_migrate)

> **언제**: `company_edge` 데이터를 새로 적재했거나 rate/share 컬럼이 비었/초기화됐을 때,
>   그리고 cri2 누적망 점수(`weight_sell_avg`/`weight_buy_avg`)를 산출·갱신할 때.
> **무엇**: nice_migrate 하나가 **두 테이블**을 갱신한다 (`--cri` 플래그로 범위 결정):

| 명령 | company_edge | company_credit_cri |
|---|---|---|
| `python -m nice_migrate` | trade_rate, sell_rate, buy_rate (+buy_rate_basis) | 안 건드림 |
| `python -m nice_migrate --cri` | 위와 동일 (**1단계로 반드시 선행**) | weight_sell_avg, weight_buy_avg |

> **왜**: `trade_rate` 는 shock 전파 입력 가중치, `sell_rate`/`buy_rate` 는 cri2 입력
>   (판매/구매망 행렬). `--cri` 는 그 입력으로 cri2 누적망 점수(등급 가중평균)까지 계산해
>   `company_credit_cri(bizno, grd_st_year)` 행에 기록한다 — rate 갱신이 항상 선행되므로
>   낡은 rate 로 점수가 계산되는 일이 없다. 갱신 컬럼 외(등급 crigrd 등 원천)는 읽기만 한다.
> **cri2 알고리즘 코드**는 `nice_ingest.pipelines.cri.pipeline`(stdlib+numpy, 희소 O(N+E) 엔진 — 2026-08-28 대규모 대응 재작성)에 있고
>   migrate 가 import 해 실행한다 — CSV 배치(`nice_ingest run cri`, DB 미사용)와 동일 core 공유.

계산식 (**2026-08 현행 — 커밋 0268794 에서 buy_rate 분모 정정됨**):
```
trade_rate(from→to, year) = sly_amt / Σ_out(from, year)     # source(셀러) 정규화
sell_rate  (=sell_share)  = sly_amt / Σ_out(source=from)    # trade_rate 와 동일 공식·값
buy_rate   (=buy_share)   = sly_amt / Σ_in(target=to)       # 바이어 "매입 총액" 정규화
```
- `trade_rate`·`sell_rate`: 같은 (셀러 from, 연도) 합 = 1 (source 정규화). ρ(전파행렬)≤1 로 수렴.
  이 거래 행 자신이 이미 source 의 Σ_out 집계에 포함되므로 분모는 항상 >0.
- `buy_rate`: 바이어의 **매입 총액**(Σ_in(to) = 그 바이어로 들어오는 모든 sly_amt 합)으로 나눔.
  이 거래 행 자신이 Σ_in 에 포함되므로 분모 항상 >0 → **항상 [0,1]**, `buy_rate_basis='target_purchases'`.
  (구 정의였던 "바이어 매출(Σ_out(to)) 분모, 무매출→0, 상한 없음"은 0268794 로 폐기 — §5-B/C 의
  과거 실행 기록 수치는 구 정의 기준이니 현재와 비교하지 말 것.)

**`--buy-fallback` 은 현행 정의에서 사실상 no-op**: 기본 계산이 이미 매입 총액(Σ_in) 기준이라
fallback 3단계(basis NULL 행 재계산)가 대상 0행이다. 하위호환으로 인자만 보존.
⚠ dry-run 의 `buy_rate_fallback_would_update` 수치는 구 정의 기준 카운트라 실제 변경 행 수가
아님 — 인용하지 말 것.

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

`--buy-fallback` 을 켤 계획이면 dry-run 에도 같이 줘야 영향 건수가 미리 보인다(안 주면 무시됨):
```bash
python -m nice_migrate --year 2026 --buy-fallback --dry-run
```
출력 예: `{"target_rows": 444, "updated": 0, "dry_run": true, "buy_rate_fallback_would_update": 64}`

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

### ③ cri2 점수까지 한 번에 (`--cri`)
```bash
python -m nice_migrate --cri --year 2024 --dry-run   # 계산만, 두 테이블 모두 무기록
python -m nice_migrate --cri --year 2024             # rate 갱신 → cri2 점수 기록
```
- 연도 매칭: `company_edge.trade_year == company_credit_cri.grd_st_year` 인 연도만 처리
  (등급 행이 없는 거래연도는 `years_skipped` 로 보고만 하고 건너뜀).
- 출력 JSON 이 `{"rate": {...}, "cri": {...}}` 로 나뉘며, cri 쪽 `rows_updated` /
  `nodes_without_grade_row`(거래망엔 있으나 등급 테이블에 행이 없어 기록 못한 노드 수) 확인.
- 무등급(NR 등)·해당 방향 유효 거래처 없음 → 해당 weight 는 NULL (cri2 규칙).

### ④ 검증 (아래 §4 SQL)

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

### 방법 E) 전용 컨테이너 — 에어갭 반입 (권장, DB 서버와 분리된 환경일 때)
`deploy/migrate/`가 `nice_migrate` 전용 이미지(sqlalchemy·psycopg·pandas·**ipython·vi** 포함,
다른 `nice_ai_*` 이미지와 완전 독립·≈370MB). 인터넷 있는 곳에서 빌드→반출, 에어갭에서
`docker load`→실행.

```bash
# (인터넷 있는 곳) 빌드 + 반출
docker build -t nice/migrate:dev -f deploy/migrate/Dockerfile .
docker save nice/migrate:dev | gzip > nice_migrate.image.tar.gz    # 매체로 이동

# (에어갭 대상 시스템) 반입 — docker-compose.deploy.yml 과 같은 폴더에서
gunzip -c nice_migrate.image.tar.gz | docker load
docker compose -f docker-compose.deploy.yml --profile migrate run --rm migrate \
    python -m nice_migrate --year 2026 --buy-fallback --dry-run
# 확인 후 --dry-run 빼고 재실행
docker compose -f docker-compose.deploy.yml --profile migrate run --rm migrate \
    python -m nice_migrate --year 2026 --buy-fallback

# 데이터 직접 조회 + LLM/ollama 호출 테스트(IPython, --shell)
# 갱신 없이 진입, engine/pd/requests/q()/llm_chat() 바로 사용 가능
docker compose -f docker-compose.deploy.yml --profile migrate run --rm migrate \
    python -m nice_migrate --shell
# 쉘 안에서:
#   q("SELECT * FROM company_edge WHERE to_bizno=:t LIMIT 20", t="1234567890")
#   llm_chat("안녕")["choices"][0]["message"]["content"]        # OpenAI 호환 chat completion
#   requests.get(os.environ["LLM_BASE_URL"].replace("/v1","") + "/api/tags")  # ollama 자체 API
```
> DB 접속 정보는 compose 의 `POSTGRES_*`(같은 폴더 `.env`, rag-server/ingestion 과 동일 변수)를,
> LLM 접속 정보는 `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY`/`LLM_REASONING_EFFORT`(rag-server 와
> 동일 변수)를 그대로 재사용한다 — migrate 만 위해 새로 값을 만들 필요 없음. `--network none`
> 기동 검증 완료(인터넷 미의존, `docs/RUNBOOK_설치.md` §1 패턴과 동일. LLM 호출 자체는 당연히
> `LLM_BASE_URL` 도달 필요 — 완전 오프라인이 아니라 "내부망 LLM 서버 도달"을 뜻함).

> **컨테이너 없이(호스트 파이썬 직접, 방법 B/C/D) `--shell` 을 쓰려면** — `ipython`/`requests` 는
> `migrate-shell` extra 라 기본 설치엔 없다. 에어갭 호스트엔 인터넷 있는 곳에서 wheel 을
> 미리 받아 반입:
> ```bash
> # (인터넷 있는 곳) wheel 다운로드 — 대상과 같은 파이썬 버전/OS 로 받을 것
> pip download -d ./migrate_shell_wheels "ipython>=8.26" "requests>=2.32"
> tar czf migrate_shell_wheels.tar.gz migrate_shell_wheels/    # 매체로 이동
>
> # (에어갭 호스트) 오프라인 설치
> tar xzf migrate_shell_wheels.tar.gz
> pip install --no-index --find-links=./migrate_shell_wheels ipython requests
> ```
> `sqlalchemy`/`psycopg`/`pandas`(base 의존성)도 호스트에 미리 없다면 같은 방식으로 받아야 한다.
> 컨테이너(방법 E)를 쓰면 이 단계 전체가 불필요 — 이미지에 다 포함돼 있다.

#### 방법 E-2) compose 없이 — `docker run` + `docker exec -it bash` 로 직접 진입 (검증됨)

compose 를 안 쓰고 이미지만 단독으로 받아 컨테이너 안에서 직접 작업하고 싶을 때. 이미지 이름은
예시로 `nice/migrate2:dev`(기존 `nice/migrate:dev` 와 별도로 구분해 반입한 경우) 사용.

```bash
# (인터넷 있는 곳) 빌드 + 반출 — 이름을 다르게 주면 기존 이미지와 구분해 따로 관리 가능
docker build -t nice/migrate2:dev -f deploy/migrate/Dockerfile .
docker save nice/migrate2:dev | gzip > nice_migrate2.image.tar.gz   # 매체로 이동

# (에어갭 대상) 반입
gunzip -c nice_migrate2.image.tar.gz | docker load
```

> ⚠ **기본 `CMD`(`python -m nice_migrate --help`)는 실행 즉시 종료된다** — `docker exec` 로
> 들어갈 대상이 없으면(컨테이너가 이미 Exited) 실패한다. 반드시 **`bash` 를 메인 프로세스로
> 살려둔 채** `-d`(백그라운드)로 띄운 뒤 `exec` 로 들어간다.

```bash
# 컨테이너를 살려둔 채로 백그라운드 기동 — DB/LLM 접속 정보는 -e 로 직접 주입
# (compose 를 안 거치므로 .env 자동 주입 없음 — --env-file <파일> 로 대체 가능)
docker run -dit --name migrate2 \
  -e POSTGRES_HOST=<DB호스트> -e POSTGRES_PORT=5432 \
  -e POSTGRES_USER=nice -e POSTGRES_PASSWORD=<pw> -e POSTGRES_DB=nice_innovation \
  -e LLM_BASE_URL=http://<LLM서버>:<port>/v1 -e LLM_MODEL=<모델> \
  nice/migrate2:dev bash

# 진입 (여러 번 반복 가능 — 컨테이너는 계속 떠있음)
docker exec -it migrate2 bash
# 안에서: python -m nice_migrate --year 2026 --dry-run
#        python -m nice_migrate --shell
#        vi 아무파일   (에디터 확인용)

# root 권한이 필요하면 (기본은 uid 1000 nice 계정)
docker exec -u root -it migrate2 bash

# 작업 종료 후 정리
docker rm -f migrate2
```
> 실측(2026-08-04): `whoami`→`nice`, `which vi`→`/usr/bin/vi`, `python -m nice_migrate --help`,
> `import IPython, pandas, requests` 전부 exec 세션 안에서 정상 확인됨.

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
| `--cri` | rate 갱신 후 **cri2 누적망 점수까지** — `company_credit_cri.weight_sell_avg`/`weight_buy_avg` 갱신 (연도 매칭 `trade_year==grd_st_year`). `--dry-run` 이면 계산만 |
| `--buy-fallback` | **현행 정의(매입 총액 기준)에선 사실상 no-op** — 하위호환 보존용. dry-run preview 수치는 구 정의 기준이라 무시 |
| `--shell` | 갱신 없이 IPython 쉘 진입(`engine`/`pd`/`requests`/`q()`/`llm_chat()` 준비됨) — 데이터 조회·핸들링 + LLM/ollama 호출 테스트용 |
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

-- (4) buy_rate 점검 — 현행(매입 총액 정규화)은 항상 [0,1]. 범위 밖이면 이상.
SELECT MIN(buy_rate), MAX(buy_rate), ROUND(AVG(buy_rate)::numeric,4) AS 평균
FROM public.company_edge WHERE buy_rate IS NOT NULL;

-- (5) buy_rate_basis 분포 — 현행 정의로 갱신된 행은 전부 target_purchases 여야 함
--     (target_sales 가 남아 있으면 0268794 이전 구 정의로 계산된 잔존 행)
SELECT buy_rate_basis, COUNT(*) FROM public.company_edge GROUP BY 1 ORDER BY 1 NULLS LAST;

-- (6) --cri 결과 점검 — 기록된 cri2 점수와 등급·연도
SELECT grd_st_year, COUNT(*) AS 등급행,
       COUNT(weight_sell_avg) AS sell채움, COUNT(weight_buy_avg) AS buy채움
FROM public.company_credit_cri GROUP BY 1 ORDER BY 1;

SELECT trim(bizno) AS bizno, crigrd, weight_sell_avg, weight_buy_avg
FROM public.company_credit_cri
WHERE weight_sell_avg IS NOT NULL OR weight_buy_avg IS NOT NULL
ORDER BY grd_st_year, bizno;
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

### 5-C. buy-fallback dry-run 확인 (2026-08-04, 미적용·조회만)
```
$ python -m nice_migrate --env-file .env --year 2026 --buy-fallback --dry-run
  {"target_rows": 444, "updated": 0, "dry_run": true, "buy_rate_fallback_would_update": 64}
```
2026년 444행 중 64행(≈14%)이 바이어 무매출로 `buy_rate=0` 상태 — `--buy-fallback` 적용 시
이 64행이 바이어 매입 총액 기준으로 재계산 대상. **실제 UPDATE 는 미실행**(에어갭 환경에서
운영 결정 후 적용 예정). 실행 시 §4-(5) SQL로 `target_purchases` 건수 = 64 인지 재검증할 것.
> ※ 위 5-B·5-C 는 **구 buy_rate 정의(바이어 매출 분모) 시절의 기록** — 0268794 정정 이후엔
> buy_rate 가 항상 [0,1]·전행 target_purchases 라 수치 양상이 다르다. 이력으로만 참고.

### 5-D. --cri 최초 실적용 — rate + cri2 점수 (2026-08-25, year 2024)
```
상태: 데이터 교체 후 rate 전 컬럼 NULL(248행, 2022~2025 각 62행).
      company_credit_cri 512행(2024/2025/2026), weight_* 전부 NULL.
$ python -m nice_migrate --env-file .env --cri --year 2024
  rate: {"target_rows": 62, "updated": 62, "rate_sum_min/max": 1.0,
         "buy_rate_min/max/avg": 0.0223/1.0/0.9839, "buy_rate_from_target_purchases": 62}
  cri:  {"2024": {"nodes": 63, "edges": 62, "graded_nodes": 2,
         "scored_buy": 51, "rows_updated": 2, "nodes_without_grade_row": 61}}
결과: weight 기록 2행 — 1018116406(AA+, buy_avg=2.0) / 3018702315(AA+, sell_avg=2.0).
원인: 거래망 63노드 중 등급 테이블 교집합이 2개뿐 + 둘 사이 거래가 한 방향 1건
      (알고리즘 정상 — 유일한 유효 상대 AA+=2점 → 가중평균 정확히 2.0). 타 연도 무오염 확인.
```

---

## 6. 주의사항

- **운영 PG 에 UPDATE** 하는 작업 → 반드시 `--dry-run` 먼저.
- **연도별로 따로 정규화**된다 (2024·2026 각각 셀러 합=1). 전 연도 한 번에 돌려도 됨.
- `sly_amt=0` 이거나 셀러의 연도 Σ_out=0 이면 그 rate=0 (분모 0 방지 처리됨).
- **`buy_rate` 는 현행 정의(매입 총액 Σ_in 분모)에서 항상 [0,1]** — 1 초과·"무매출→0" 은
  구 정의(0268794 이전) 이야기다. §4-(4) 에서 범위 밖이 보이면 이상 신호.
- 세 컬럼이 **한 트랜잭션**에서 갱신된다(부분 반영 없음). `--no-shares` 로 공유율만 건너뛸 수 있음.
- compose 의 `up` 대상이 **아니다** — 상시 가동 X, 데이터 적재 후 **1회성 CLI/배치**로 실행
  (`docker-compose.deploy.yml` 의 `migrate` 서비스도 `profiles: ["migrate"]` + `run --rm` 전용).
- `--buy-fallback` 은 하위호환용 no-op — 새 작업에서 쓸 이유가 없다. preview 수치도 무시.
- **`--cri` 는 `company_credit_cri` 에 UPDATE** 한다 — weight 두 컬럼만 쓰고 등급 등 원천
  컬럼은 읽기 전용. 결과가 희소하면 버그가 아니라 **등급∩거래망 교집합**부터 확인(§5-D).
- migrate 이미지는 `--cri` 가 cri2 core 를 import 하므로 **`src/nice_ingest` 가 이미지에
  포함**돼야 한다 (deploy/migrate/Dockerfile 에 COPY 반영됨 — 구버전 이미지로는 --cri 불가).
- 관련: [`README.md`](../README.md) §1-A(nice_migrate), [`CLAUDE.md`](../CLAUDE.md).

---

기호: Σ_out(X)=연도별 X 의 총매출(from=X 기준 sly_amt 합), ρ=rho(전파행렬 spectral radius)
