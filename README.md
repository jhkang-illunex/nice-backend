# nice-backend

NICE Open Innovation PoC — 공급망/수요망 충격 시뮬레이션 백엔드.

![CI](https://github.com/jhkang-illunex/nice-backend/actions/workflows/ci.yml/badge.svg)

> 배지는 GitHub repo 가 public 일 때만 외부에서 렌더링됩니다. private 인 경우
> 로그인한 멤버에게만 정상 표시되며, README 를 익명 미러에서 볼 때는 broken
> 이미지로 보일 수 있습니다.

## 현재 상태 — 데이터 수령 대기

| 영역 | 상태 |
|---|---|
| 인프라 (PG/Neo4j/Redis) | docker-compose 기동, healthy |
| PG 스키마 | 8 테이블 + MV 2 + 확장 3 (vector/pg_trgm/btree_gin) 적용 완료 |
| Neo4j 스키마 | 제약 9 + 인덱스 17 + APOC 5.24 적용 완료 |
| Python 모듈 | 1~4주차 (PoC 1차 시연 가능 분량) 구현 + 41 단위 테스트 pass |
| ETL | 디렉토리 컨벤션 + generic upload(임의 컬럼명 매핑) 둘 다 작동 |
| **레코드** | **0** — 실 데이터 수령 후 적재 시작 |

**실 데이터가 도착하면** → [docs/DATA_INTAKE.md](docs/DATA_INTAKE.md) 의 6단계
체크리스트 그대로 실행.

## 설계 문서

`docs/` 디렉토리 참조.

- `NICE_폴리글랏_아키텍처_설계서.docx`
- `NICE_Neo4j_그래프모델_설계서_v2.3.docx`
- `NICE_Python_구현명세서.docx`
- `ARCHITECTURE_DECISIONS.md` — 부트스트랩 시점 ADR

## 스토리지 토폴로지

| 저장소 | 역할 | 인스턴스 |
|---|---|---|
| Neo4j 5.x Community | 그래프 본질 (Firm, SUPPLIES, Scenario, drill-down) | docker-compose 신규 싱글 노드 |
| PostgreSQL 16 + pgvector | 결과 테이블, 정렬·집계, 검색 | docker-compose 신규 |
| Redis 7 | KPI/좌표/시계열 캐시 | docker-compose 신규 |

근거는 `docs/ARCHITECTURE_DECISIONS.md` ADR-001/002 참조.

## 빠른 시작

```bash
cp .env.example .env

# 인프라 기동 (Neo4j + PG + Redis)
docker compose up -d

# Python 환경
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 헬스체크
uvicorn nice_poc.api.main:app --reload --port 8000
curl http://localhost:8000/health
curl http://localhost:8000/health/deep
```

PostgreSQL 스키마는 컨테이너 최초 기동 시 `deploy/postgres/init/*.sql` 이
자동 적용됩니다. 재적용이 필요하면 볼륨 삭제 후 재기동:

```bash
docker compose down -v && docker compose up -d
```

이후 스키마 변경은 Alembic 으로 추적합니다 (baseline `0001_baseline` 박힘, ADR-004):

```bash
.venv/bin/alembic current                       # 현재 revision 확인
.venv/bin/alembic revision -m "add column X"    # 신규 마이그레이션 작성
.venv/bin/alembic upgrade head                  # 라이브 DB 적용
```

Neo4j 제약/인덱스는 1회만 수동 적용 (구현명세서 §1 의존 모듈 진입 전):

```bash
docker exec -i nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" < deploy/neo4j/constraints.cypher
docker exec -i nice-neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" < deploy/neo4j/indexes.cypher
```

## 디렉토리 구조

```
.
├── docs/                       # 설계서 (docx) + ADR
├── deploy/
│   ├── postgres/init/          # docker entrypoint 가 자동 적용
│   └── neo4j/                  # 수동 적용 cypher
├── src/nice_poc/
│   ├── config/                 # pydantic-settings (.env 로딩)
│   ├── db/                     # Neo4j / PG / Redis 클라이언트
│   ├── api/                    # FastAPI 진입점 + 라우터
│   ├── data/                   # Neo4j → DataFrame + scipy.sparse
│   ├── matrix/                 # A / A1 / H / R / B 행렬
│   ├── shock/                  # 8종 시나리오 Δy
│   ├── propagation/            # BiCGSTAB / Sparse LU / 한주동 BFS
│   ├── indicator/              # Edge Value / TIS / Network CRI
│   ├── safety/                 # ρ(A) / row-normalize / cap / hub
│   ├── estimate/               # 보조 ML (extras=ml)
│   └── result/                 # impact_table + :IMPACTS 적재
├── tests/
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

`cache/`(PoC 2차), `sync/`(운영 1.0), `search/`(운영 1.1) 은 phase 진입 시
신설합니다. ADR-003 참조.

## ETL — 원천 → PG/Neo4j 적재

```bash
# 디렉토리 레이아웃
<root>/
├── firms.csv              # firm_id, biz_no, rep_bizno, firm_name, sector_code, base_year, sales_year_fin, ...
├── supplies.csv           # source_id, target_id, year, amount, source_cate, target_cate, purchase_weight, sales_weight, ...
├── trade.csv              # firm_id, hs6, iso_alpha2, year, direction(EXP/IMP), amount, weight_hs, weight_nation, rank
└── masters/
    ├── sectors.csv        # code, name, level, parent_code, color
    ├── hs_codes.csv       # code, name, hs2, hs4, elasticity
    └── countries.csv      # iso_alpha2, name_kr, name_en

# 실행 (적재 순서가 강제됨: masters → firms → supplies → trade)
python -m nice_poc.etl masters  <root>
python -m nice_poc.etl firms    <root>
python -m nice_poc.etl supplies <root>
python -m nice_poc.etl trade    <root>

# 또는 한 번에
python -m nice_poc.etl all      <root>
```

어댑터를 교체하면 원천을 바꿀 수 있습니다 — `src/nice_poc/etl/sources/` 의
Protocol 을 만족하면 됩니다(예: `PostgresRawSource` 가 kis_em_* 등 RAW 테이블에서
직접 읽도록).

모든 적재는 MERGE/UPSERT 로 idempotent — 동일 입력 재실행 안전.

### 임의 CSV 업로드 (컬럼명이 다를 때)

원천 CSV 의 컬럼명이 도메인 파이프라인과 다르면 `upload-pg` / `upload-neo4j`
서브커맨드 + `--rename` 매핑 사용. 자세한 컬럼 명세는 `docs/CSV_SCHEMA.md`.

```bash
# 한국어 컬럼명을 표준 컬럼명으로 매핑하면서 PG 에 UPSERT
python -m nice_poc.etl upload-pg /data/raw_firms.csv \
    --table firms --pk firm_id \
    --rename "기업ID=firm_id,기업명=firm_name,업종=sector_code,재무매출=sales_year_fin"

# 임의 Cypher 적용 (노드 + 관계 동시)
python -m nice_poc.etl upload-neo4j /data/raw_firms.csv \
    --cypher-file /tmp/firms_merge.cypher \
    --rename "기업ID=firm_id,기업명=firm_name"

# 실제 적재 전 행수 + 컬럼 누락 검증만
python -m nice_poc.etl upload-pg /data/raw_firms.csv \
    --table firms --pk firm_id --dry-run
```

## 12주 마일스톤 (Python 구현명세서 §10)

| 주차 | 모듈 | 산출물 |
|---|---|---|
| 1 | `data/`, `matrix/matrix_H.py`, `safety/spectral_radius.py` | H 생성, ρ(H) 측정 |
| 2 | `shock/`, `propagation/bicgstab.py`, `propagation/leontief.py` | 단일 시나리오 Δx |
| 3 | `safety/max_delta_cap.py`, `indicator/edge_value.py`, `tis.py` | 안전장치 + 화면 지표 |
| 4 | `result/to_neo4j.py`, `result/aggregate.py`, `data/load_graph.py` | :IMPACTS 적재 + 시연 가능 |
| 5 | `matrix/matrix_R.py`, `matrix_B.py` | α 슬라이더 |
| 6 | `propagation/shortest_path.py`, `indicator/network_cri.py` | 한주동 + CRI 가중 |
| 7 | `estimate/sales_estimator.py`, `asset_estimator.py` | ML 결측 보완 |
| 8 | `tests/` 전체 + 부하 테스트 | 검증 보고서 |
