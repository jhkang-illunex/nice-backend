# 실 데이터 수령 절차

본 프로젝트는 현재 **데이터 수령 대기 상태** 다. 코드/인프라/스키마는 모두
준비됐고 데이터만 비어 있다. CSV 가 도착하면 본 문서의 6단계를 따라가면
즉시 시뮬레이션이 가능하다.

---

## 0. 현재 상태 (수령 대기) 체크

```bash
docker ps --filter name=nice- --format 'table {{.Names}}\t{{.Status}}'
# nice-pg / nice-neo4j / nice-redis 모두 "healthy" 여야 함

.venv/bin/pytest -q     # 41 passed
```

| 항목 | 상태 |
|---|---|
| 컨테이너 | PG 16 + Neo4j 5.24 Community + Redis 7 — healthy |
| PG 테이블 | 8 + MV 2 (firms / impacts / scenarios / shocks / simulation_runs / sectors / hs_codes / countries / mv_impacts_by_sector / mv_impacts_by_hq) |
| PG 확장 | `vector` / `pg_trgm` / `btree_gin` |
| Neo4j 제약 | 9 (PK UNIQUE 전체) |
| Neo4j 인덱스 | 17 (RANGE + FULLTEXT + 관계 프로퍼티) |
| 데이터 | **모두 0** |
| 단위 테스트 | 41/41 pass (~1.5s) |

컨테이너가 멈춰 있으면:
```bash
cp .env.example .env   # 처음 한 번만 (호스트 포트: 15432/17687/17474/16379)
docker compose up -d
```

---

## 1. CSV 컬럼 점검 (`docs/CSV_SCHEMA.md`)

받은 CSV 의 컬럼명을 `docs/CSV_SCHEMA.md` 의 매트릭스와 비교한다.

- **일치하면** → 디렉토리 컨벤션 ETL (Step 4-A)
- **다르면** → `--rename` 매핑으로 generic upload (Step 4-B)
- **한국어/특수 인코딩** → `--encoding`, `--delimiter` 명시

---

## 2. (1회만) Neo4j 제약/인덱스 적용

`docker compose up -d` 후 컨테이너를 **신규 생성한 직후 1회만** 적용한다.
(이미 적용돼 있으면 `IF NOT EXISTS` 가 그대로 통과)

```bash
docker exec -i nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
    < deploy/neo4j/constraints.cypher
docker exec -i nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
    < deploy/neo4j/indexes.cypher
```

PG 스키마는 `deploy/postgres/init/*.sql` 가 컨테이너 최초 기동 시 자동 적용.

---

## 3. 데이터 적재 — 적재 순서가 강제됨

FK / Neo4j MATCH 의존성 때문에 다음 순서를 지켜야 한다.

```
masters (sectors → hs_codes → countries)
  → firms
    → supplies (Neo4j SUPPLIES 엣지)
    → trade   (Neo4j EXPORTS_TO / IMPORTS_FROM / TRADES_PRODUCT)
```

### 4-A. CSV 컬럼이 표준과 일치하는 경우

```bash
python -m nice_poc.etl all /path/to/data
# 또는 단계별로:
python -m nice_poc.etl masters  /path/to/data
python -m nice_poc.etl firms    /path/to/data
python -m nice_poc.etl supplies /path/to/data
python -m nice_poc.etl trade    /path/to/data
```

원천 디렉토리 레이아웃:
```
<root>/
├── firms.csv
├── supplies.csv
├── trade.csv
└── masters/
    ├── sectors.csv
    ├── hs_codes.csv
    └── countries.csv
```

### 4-B. CSV 컬럼이 다른 경우 (generic upload)

```bash
# PG 테이블에 UPSERT (컬럼 매핑)
python -m nice_poc.etl upload-pg /path/to/raw_firms.csv \
    --table firms --pk firm_id \
    --rename "기업ID=firm_id,기업명=firm_name,업종=sector_code,재무매출=sales_year_fin"

# Neo4j 에 임의 Cypher 적재
python -m nice_poc.etl upload-neo4j /path/to/raw_firms.csv \
    --cypher-file my_merge.cypher \
    --rename "기업ID=firm_id,기업명=firm_name"

# 실제 적재 전 컬럼 누락 검증
python -m nice_poc.etl upload-pg /path/to/raw_firms.csv \
    --table firms --pk firm_id --dry-run
```

---

## 4. 적재 검증

```bash
# PG 카운트
docker exec nice-pg psql -U nice -d nice -c "
SELECT 'firms='||count(*) FROM firms
UNION ALL SELECT 'sectors='||count(*) FROM sectors
UNION ALL SELECT 'hs_codes='||count(*) FROM hs_codes
UNION ALL SELECT 'countries='||count(*) FROM countries;"

# Neo4j 노드/관계 카운트
docker exec nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
    "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY label;"
docker exec nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
    "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY rel;"

# /health/deep (FastAPI 측에서 3저장소 연결 확인)
uvicorn nice_poc.api.main:app --port 8000 &
curl http://localhost:8000/health/deep
```

---

## 5. 단일 시뮬 1회 (적재 검증의 마지막 단계)

end-to-end 모듈이 모두 연결되는지 확인. 다음을
`scripts/run_simulation.py` 등으로 저장하고 실행한다.

```python
from nice_poc.data import load_graph
from nice_poc.matrix import matrix_H
from nice_poc.safety import spectral_radius, max_delta_cap
from nice_poc.shock import direct_shock
from nice_poc.shock.scenario import Shock
from nice_poc.propagation import leontief
from nice_poc.indicator import tis as tis_mod
from nice_poc.result import impact_record, to_neo4j, aggregate
import pandas as pd

YEAR = 2024
RUN_ID = "RUN_001"
SCENARIO_ID = "SC_001"

# 1) Neo4j → DataFrame
data = load_graph.from_neo4j(year=YEAR)
firms, edges, exports = data["firms"], data["edges"], data["exports"]

# 2) H 행렬 + ρ 안전장치
h = matrix_H.build(edges, firms, year=YEAR)
H_safe, rho, normalized = spectral_radius.check_and_normalize(h.H)

# 3) Shock 정의 — TARIFF / GDP / B2C_REVENUE / GOV_REVENUE /
#    IMPORT_PRICE / IMPORT_SHUTDOWN / DOMESTIC_PRICE / DOMESTIC_SHUTDOWN
shock = Shock(
    shock_id="SH_001", scenario_id=SCENARIO_ID,
    shock_type="수출", target_type="HS6", input_type="TARIFF",
    target_value="854231", target_nation=["US"],
    before_tariff=0.0, after_tariff=0.25,
    pass_through=1.0, price_elasticity=-1.2,
    duration_month=12,
)

# 4) Δy 1차 충격
dy = direct_shock.compute(shock, firms, exports=exports)["delta_revenue"]
delta_y = dy.reindex(h.firm_ids, fill_value=0.0)

# 5) BiCGSTAB 후방 파급
split = leontief.propagate_demand_split(H_safe, delta_y.to_numpy())

# 6) max-delta cap
sales = firms["sales_year_fin"].astype("float64").fillna(0)
init_s = pd.Series(split["initial"], index=h.firm_ids)
total_s = pd.Series(split["total"], index=h.firm_ids)
capped_total, flag = max_delta_cap.cap_revenue(total_s, sales)
capped_initial, _ = max_delta_cap.cap_revenue(init_s, sales)
capped_prop = capped_total - capped_initial

# 7) TIS
tis_df = tis_mod.compute(capped_total, firms)

# 8) impact_table
table = impact_record.build_impact_table(h.firm_ids, demand={
    "initial":     capped_initial.to_numpy(),
    "propagation": capped_prop.to_numpy(),
    "total":       capped_total.to_numpy(),
})

# 9) Neo4j :IMPACTS 적재
to_neo4j.write_impacts(
    run_id=RUN_ID, scenario_id=SCENARIO_ID, target_year=YEAR,
    impact_table=table, impact_score=tis_df["tis"],
    capped_flag=flag, rho_a=float(rho),
    capped_ratio=max_delta_cap.capped_ratio(flag),
)

# 10) Summary 카드 12키
print(aggregate.summary_card_full(table, run_id=RUN_ID))
```

성공 기준:
- `nodes` count 가 Firm 수만큼
- `MATCH (r:SimulationRun {run_id:'RUN_001'})-[i:IMPACTS]->()` 카운트가 firm 수와 일치
- Summary 카드의 `Revenue_total_Sum` 이 0 이 아님 (Δy 가 적용된 시나리오면)

---

## 6. 데이터 초기화 (재적재 시)

스키마/제약/인덱스는 **유지**하고 레코드만 비운다.

```bash
docker exec nice-pg psql -U nice -d nice -c "
    TRUNCATE impacts, simulation_runs, shocks, scenarios,
             firms, sectors, hs_codes, countries
    RESTART IDENTITY CASCADE;"

docker exec nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
    "MATCH (n) DETACH DELETE n;"

# MV 재생성 (CONCURRENTLY 는 처음 한 번은 안 됨)
docker exec nice-pg psql -U nice -d nice -c "
    REFRESH MATERIALIZED VIEW mv_impacts_by_sector;
    REFRESH MATERIALIZED VIEW mv_impacts_by_hq;"
```

전체를 처음부터 다시 시작하려면 (위험 — 모든 데이터 손실):
```bash
docker compose down -v   # 볼륨까지 삭제
docker compose up -d     # PG init/*.sql 재실행
# Neo4j constraints/indexes 재적용 필요 (Step 2)
```

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `UNIQUE constraint violation` (Firm) | 동일 firm_id 중복 행 | CSV 에서 중복 제거 또는 MERGE 가 idempotent 라 그냥 재실행 |
| `firm_id 가 not found` (Neo4j MATCH) | supplies 가 firms 보다 먼저 적재됨 | 순서 지키기: masters → firms → supplies |
| 한글 깨짐 | encoding 미지정 | `--encoding utf-8` (기본값) 또는 `--encoding cp949` |
| `KeyError: missing required columns` | CSV 컬럼명이 표준과 다름 | `--rename old=new,...` 또는 `docs/CSV_SCHEMA.md` 의 컬럼명으로 재작성 |
| `ρ ≥ 1` 경고 + 결과 발산 | H 행렬에 자체 사이클 또는 spectral radius 1 초과 | `check_and_normalize()` 가 자동 row-normalize. 그래도 발산하면 H 빌드 분모 확인 |
| BiCGSTAB `SolverError` | (I-H) 가 singular 또는 수치 불안정 | `safety/spectral_radius.row_normalize` 강제 적용 후 재시도 |

---

## 데이터 도착 전 점검 명령

```bash
# 한 번에 모두 보기
docker ps --filter name=nice- --format 'table {{.Names}}\t{{.Status}}'
docker exec nice-pg psql -U nice -d nice -c "\dt"
docker exec nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
    "SHOW CONSTRAINTS YIELD name RETURN count(name);"
.venv/bin/pytest -q
```

위 4개가 모두 정상이면 **언제든 데이터 적재 가능**.
