# 작업 내역 (Bootstrap → 현재)

본 문서는 **이미 완료한 작업**의 종합 정리다. 미래 작업은
[`docs/POST_INTAKE_TASKS.md`](POST_INTAKE_TASKS.md) 를 참조.

작성 시점 기준 형상: **데이터 수령 대기, 4 commit, 49/49 tests pass.**

---

## 1. 타임라인 (commit 단위)

| SHA | 제목 | 변경량 | 핵심 산출 |
|---|---|---|---|
| `42025d9` | Initial commit | +220 / 2 files | README + .gitignore |
| `0b98be4` | PoC 1차 시연 가능 형상까지 부트스트랩 | +3,761 / 77 files | 인프라 + 스키마 + 1~4주차 모듈 + ETL + 문서 |
| `59062c1` | PoC 1차 baseline 보강 | +1,119 / 54 files | Alembic + CI + FastAPI 라우터 + lint/format |
| `505d5e3` | result/dual_write — 폴리글랏 §5.5 | +346 / 6 files | dual-write 단일 진입점 + MV refresh |

누적: **4,026 라인 추가 / 11 라인 삭제 / 76 고유 파일**.

---

## 2. 마일스톤 진행 매트릭스 (Python 구현명세서 §10)

| 주차 | 모듈 / 산출물 | 상태 | 단위 테스트 |
|---|---|---|---|
| 1 | `data/load_graph`, `matrix/matrix_H`, `safety/spectral_radius` | ✅ 구현 | 9개 (test_matrix_H + test_spectral) |
| 2 | `shock/*`, `propagation/bicgstab`, `propagation/leontief` | ✅ 구현 | 10개 (test_demand_tariff + test_supply + test_bicgstab) |
| 3 | `safety/max_delta_cap`, `indicator/edge_value`, `indicator/tis` | ✅ 구현 | 6개 (test_cap + test_indicator) |
| 4 | `result/{impact_record,to_neo4j,aggregate}` | ✅ 구현 | 5개 (test_aggregate) |
| **§5.5** | **`result/dual_write`** (폴리글랏 §5.5) | **✅ 구현** | **3개 (test_dual_write)** |
| 5 | `matrix/matrix_R`, `matrix/matrix_B` (α 슬라이더) | ⏳ 미구현 | — |
| 6 | `propagation/shortest_path` (한주동), `indicator/network_cri` | ⏳ 미구현 | — |
| 7 | `estimate/sales_estimator`, `estimate/asset_estimator` | ⏳ 미구현 | — |
| 8 | 통합 검증 + 부하 테스트 | ⏳ 미구현 | — |

PoC 1차 시연 가능 라인(4주차) + 폴리글랏 §5.5 까지 **5단계 모두 완료**.

---

## 3. 인프라

| 컴포넌트 | 버전 / 사양 | 상태 |
|---|---|---|
| Neo4j | 5.24 Community + APOC 5.24 | 호스트 17474/17687, healthy |
| PostgreSQL | 16 + pgvector + pg_trgm + btree_gin | 호스트 15432, healthy |
| Redis | 7 alpine | 호스트 16379, healthy |
| Python | 3.12.10 (pyenv) | .venv/, dev extras 설치 |
| Docker Compose | v2.20.2 | 컨테이너 3종 모두 healthy |

호스트 포트는 기존 Neo4j 클러스터(7474/7687/7476/7689/7477/7690)와 Redis(6379)
점유 회피 위해 **프리픽스 1** 매핑.

---

## 4. 데이터 스키마

### PostgreSQL (`deploy/postgres/init/*.sql`, Alembic baseline `0001_baseline`)

| 객체 | 수 | 비고 |
|---|---|---|
| 테이블 | 8 | firms / impacts / scenarios / shocks / simulation_runs / sectors / hs_codes / countries |
| 머터리얼라이즈드 뷰 | 2 | mv_impacts_by_sector / mv_impacts_by_hq |
| 확장 | 3 | vector / pg_trgm / btree_gin |
| firms 인덱스 | 7 | bizno / rep_bizno / sector / base_year / name_trgm / name_tsv / name_emb (HNSW) |
| impacts 인덱스 | 2 | (run_id, abs(revenue_sum) DESC) / (scenario_group_id) |
| Alembic | 1 baseline | `0001_baseline` stamp 완료, 이후 변경은 revision 작성 |

### Neo4j

| 객체 | 수 | 비고 |
|---|---|---|
| 제약 | 9 | 모든 PK UNIQUE (Firm/Headquarter/Sector/HSCode/Country/Year/Scenario/Shock/SimulationRun) |
| 인덱스 | 17 | Firm 필터 3 + 풀텍스트 1 + 관계 프로퍼티 2 + LOOKUP 자동생성 + PK RANGE |
| 라벨 (의도) | 9 | 그래프 모델 v2.3 §2.1 그대로 |
| 관계 (의도) | 13 | 그래프 모델 v2.3 §2.2 그대로 |

### 라이브 레코드

**모두 0** — 수령 대기 상태.

---

## 5. Python 패키지 구조

```
src/nice_poc/                      # 18 디렉토리 / 50+ 파일 / 2,714 줄
├── __init__.py
├── config/                        # pydantic-settings (env 로딩)
├── db/                            # neo4j / postgres / redis 클라이언트
├── api/                           # FastAPI 진입점
│   ├── main.py                    # 8 라우터 include
│   ├── schemas.py                 # Pydantic v2 모델 (15+ 클래스)
│   └── routers/                   # health + 7 비즈니스 라우터
├── data/load_graph.py             # Neo4j → pandas DataFrame
├── matrix/matrix_H.py             # H 행렬 빌더 (분모 우선순위)
├── shock/                         # 8 input_type Δy 산식 (v1.1 부호 보정)
│   ├── scenario.py (Shock dataclass)
│   ├── rates.py (8개 rate 함수 + dispatcher)
│   └── direct_shock.py
├── propagation/
│   ├── bicgstab.py (+ GMRES 폴백)
│   └── leontief.py (propagate_demand_split + propagate_supply_split)
├── indicator/
│   ├── edge_value.py
│   └── tis.py (Exposure × Risk)
├── safety/
│   ├── spectral_radius.py (ARPACK + power iter + dense 폴백)
│   └── max_delta_cap.py
├── result/
│   ├── impact_record.py (9컬럼 빌더)
│   ├── to_neo4j.py (:SimulationRun + :IMPACTS)
│   ├── dual_write.py (★ 폴리글랏 §5.5 단일 진입점)
│   └── aggregate.py (Summary 12키 + by_scenario_seq + affected_firms)
├── estimate/                      # 빈 (7주차)
└── etl/                           # 3축 분리 + generic upload
    ├── sources/ (Protocol + CsvSource)
    ├── sinks/ (PgSink + Neo4jSink)
    ├── pipelines/ (masters + firms + supplies + trade)
    ├── upload.py (임의 CSV + --rename + --dry-run)
    └── __main__.py (argparse CLI)
```

미신설: `cache/` (PoC 2차), `sync/` (운영 1.0), `search/` (운영 1.1) — ADR-003.

---

## 6. ETL 능력

| 진입점 | 용도 | 대상 |
|---|---|---|
| `python -m nice_poc.etl all <root>` | 표준 컬럼 CSV 일괄 적재 | masters → firms → supplies → trade |
| `python -m nice_poc.etl {masters\|firms\|supplies\|trade} <root>` | 단계별 | 각 도메인 |
| `python -m nice_poc.etl upload-pg <csv> --table T --pk PK [--rename …]` | 임의 CSV → PG UPSERT | 컬럼명이 다를 때 |
| `python -m nice_poc.etl upload-neo4j <csv> --cypher-file F` | 임의 CSV → Cypher | ad-hoc 노드/관계 적재 |
| `--dry-run` | 행수 + 컬럼 누락 검증 | 적재 전 점검 |

모든 적재 idempotent (MERGE / UPSERT).

라이브 검증:
- 합성 5 firms / 6 supplies / 4 trade — ETL 적재 완료 후 즉시 정리됨
- 한국어 컬럼명 (`기업ID/기업명`) → `--rename` 매핑으로 PG/Neo4j 적재 검증
- 재실행 idempotency 검증 (MERGE 정상)

---

## 7. 시뮬레이션 파이프라인 (end-to-end 검증됨)

```
load_graph.from_neo4j(year)
    → firms / edges / exports DataFrame
matrix_H.build()
    → 분모 우선순위 (sales_year_fin > vat_fs_est_sales > ml_estimate_sales)
    → B2C/GOV 제외, 열 합 > 1 정규화
spectral_radius.check_and_normalize()
    → ARPACK / power iter / dense 3단 폴백, ρ ≥ 0.95 면 row_normalize
Shock(input_type=...)
    → 8 input_type 모두 + v1.1 §11.2 부호 보정
direct_shock.compute(shock, firms, exports=)
    → Δy revenue / Δy cost
leontief.propagate_demand_split / propagate_supply_split
    → BiCGSTAB + GMRES 폴백, initial/propagation/total 3-way 분리
max_delta_cap.cap_revenue / cap_cost
    → |Δ| ≤ |base| 보정, capped_flag 반환
tis.compute()
    → Exposure × Risk
impact_record.build_impact_table()
    → 9컬럼 DataFrame (DEMAND+SUPPLY 합산, MIXED 정합)
dual_write.write_impacts_dual()  ★ 단일 진입점
    → PG simulation_runs UPSERT
    → Neo4j :SimulationRun + :IMPACTS
    → PG impacts UPSERT
    → PG mv_impacts_by_sector + mv_impacts_by_hq REFRESH
aggregate.summary_card_full()
    → Summary 12키
```

라이브 검증 결과 (5 firms / TARIFF 854231→US):
- Δy = 60,000,000 (산식 검증 — `0.04 × 1,500M = 60M`)
- 후방 파급 F00001 = 2,823,529 (H[F00001,F00002] × 60M)
- PG impacts.revenue_sum NUMERIC(18,2) 정확 / Neo4j Float 동일
- MV mv_impacts_by_sector: C26 = 62.8M / 2 firms (정합)
- DualWriteReport: pg_runs=1, neo4j_impacts=5, pg_impacts=5, mv_refreshed=2

---

## 8. FastAPI

OpenAPI **13 paths** (실 구현 미완 — 모두 `501 Not Implemented`).

| 영역 | Method + Path | 화면 |
|---|---|---|
| health | GET /health, GET /health/deep | — |
| KPI | GET /api/run/{run_id}/kpi | ① |
| Scenario | POST /api/scenario | ② |
| Run | POST /api/run, GET /api/run/{run_id}/firms | ②⑤ |
| Network | GET /api/run/{run_id}/network | ③ |
| Firm | GET /api/run/{run_id}/firm/{firm_id}, /path | ④ |
| Aggregate | GET /api/run/{run_id}/by-sector, /timeseries | ⑥⑦ |
| Search | GET /api/search/autocomplete, /semantic | 검색 |

Pydantic 스키마 15+ 클래스 (KpiCard / FirmImpact / NetworkSubgraph / Paginated[T] / 등).
프론트엔드 팀에 `/openapi.json` 으로 contract 공유 가능.

---

## 9. 코드 품질 & CI

| 메트릭 | 결과 |
|---|---|
| ruff check | **0 issues** |
| ruff format | **69 files OK** (4 디렉토리 일괄 적용) |
| mypy | **55 src files success** (pyproject `ignore_missing_imports`) |
| pytest | **49/49 pass / 1.7s** |
| 단위 테스트 | 13 파일 / 865 줄 |
| CI | `.github/workflows/ci.yml` (push/PR → ruff + pytest, Python 3.12) |

---

## 10. 문서 산출물

| 문서 | 줄수 | 용도 |
|---|---|---|
| `README.md` | ~170 | 빠른 시작 + 현재 상태 + 디렉토리 트리 + ETL 사용법 |
| `docs/ARCHITECTURE_DECISIONS.md` | ~135 | ADR 5건 (Neo4j 토폴로지 / PG / 패키지명 / DDL 적재 / 의존성) |
| `docs/CSV_SCHEMA.md` | ~180 | 6 도메인 CSV 컬럼 매트릭스 |
| `docs/DATA_INTAKE.md` | ~275 | 데이터 수령 시 6단계 + end-to-end 스니펫 + 트러블슈팅 |
| `docs/PROGRESS.md` | (본 문서) | 작업 내역 — 불변 / append-only |
| `docs/POST_INTAKE_TASKS.md` | (다음) | 데이터 도착 후 작업 큐 — 가변 |
| 설계서 (docx) | — | NICE 폴리글랏 / Neo4j 그래프모델 v2.3 / Python 구현명세서 v1.1 |

---

## 11. 알려진 한계 (데이터 도착 후 보강 예정)

순위 매겨진 잔여 작업은 [`POST_INTAKE_TASKS.md`](POST_INTAKE_TASKS.md) 참조.

요약:
- `ui_severity` 분류 룰 — 스펙 미정 → dual_write 가 NULL 적재 중
- B 행렬 — Supply 매출 전방이 H.T 폴백 (5주차)
- Hub-aware BFS — 안전장치 4종 중 4번 (6주차)
- Redis 캐시 (`cache/`) — PoC 2차 진입 시 신설
- `sync/` 모듈 — 운영 1.0 진입 시 (firms_master 양방향, redis warmup)
- `search/` 모듈 + mecab-ko + sentence-transformers — 운영 1.1
