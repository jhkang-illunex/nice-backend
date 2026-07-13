# 데이터 도착 후 작업 큐

본 문서는 **실 데이터 수령 직후의 작업 백로그**다. 우선순위 / 시작 트리거 /
의존 데이터 / 예상 시간 / 산출물 4축을 박아 — 데이터가 와도 작업 순서를
다시 협의할 필요 없게 한다.

데이터 적재 절차는 [`DATA_INTAKE.md`](DATA_INTAKE.md) 의 6단계.
지금까지 한 작업 정리는 [`PROGRESS.md`](PROGRESS.md).
DATA_INTAKE.md 6단계 끝나는 즉시 본 문서의 **P0** 진입.

---

## 우선순위 범례

| 순위 | 의미 | 시작 시점 |
|---|---|---|
| **P0** | 즉시 (적재 직후 ~1일) | DATA_INTAKE 6단계 완료 즉시 |
| **P1** | 5주차 산출물 | P0 완료 후 |
| **P2** | 6주차 산출물 | 5주차 PR 머지 후 |
| **P3** | 7주차 산출물 | 6주차 PR 머지 후 |
| **P4** | 8주차 — 시연 직전 | PoC 종료 직전 |
| **P5** | PoC 2차 (캐시) | PoC 1차 종료 후 |
| **P6** | 운영 1.0 (sync/) | PoC 2차 종료 후 |
| **P7** | 운영 1.1 (search/) | 운영 1.0 안정화 후 |

---

## P0 — 즉시 (~1일)

### P0-1. `ui_severity` 분류 룰 확정

- **현황**: `dual_write` 가 `impacts.ui_severity` 를 NULL 적재 — 화면 색상/필터에 사용되지만 룰이 명세서에 미정
- **시작 트리거**: 데이터 적재 후 영향 분포 1회 관찰 (시뮬 1회 → 화면 ⑤ 영향 기업 리스트 분포 확인)
- **의존**: 실 데이터의 `revenue_sum` 분포 (예: 1차 충격 90 percentile 이 100M 인지 10B 인지)
- **결정 항목**: 임계값 — 예) `|revenue_sum| > 1B → CRITICAL`, `> 100M → WARNING`, `else → INFO`
- **위치**: `src/nice_poc/indicator/severity.py` 신설 (단일 함수 `classify(impact_table) -> pd.Series`) + `dual_write` 에서 호출
- **산출물**: 함수 1개 + 단위 테스트 + Alembic revision (분류 룰이 fixed enum 으로 굳어지면 `ui_severity` 컬럼 CHECK 제약 추가)
- **예상 시간**: 2~4시간

### P0-2. ETL 컬럼 매핑 확정 + ETL 자동화

- **현황**: `docs/CSV_SCHEMA.md` 의 표준 컬럼명을 가정. 실 데이터는 컬럼명이 다를 가능성이 높음 (한글, kis_em..s_em001 같은 RAW 컬럼)
- **시작 트리거**: 데이터 디렉토리 받자마자
- **의존**: 받은 CSV/덤프의 헤더
- **결정 항목**: ① 표준 컬럼으로 사전 변환 (Excel/pandas one-shot) vs ② `--rename` 매핑 반복 사용
- **위치**: `scripts/normalize_raw.py` (선택, 사전 변환 시) 또는 운영 매뉴얼에 `--rename` 명령 기록
- **산출물**: 표준화된 CSV 디렉토리 + `python -m nice_poc.etl all <dir>` 1회 적재 성공
- **예상 시간**: 1~3시간 (데이터 모양에 따라)

### P0-3. 1차 시뮬 + 결과 정합성 검증

- **현황**: 합성 데이터로만 end-to-end 검증. 실 데이터 분포에서 안전장치(ρ check, max-delta cap) 가 의도대로 작동하는지 미확인
- **시작 트리거**: P0-1, P0-2 완료 후
- **의존**: 적재 완료 + 시연용 시나리오 1건
- **결정 항목**: 첫 시연용 Shock 선정 — TARIFF (수출 충격) 또는 KSIC 광범위 B2C 중
- **산출물**: 시뮬 1회 결과 + Summary 카드 12키 검증 보고 (`Revenue_total_Sum` 등이 합리적 범위)
- **예상 시간**: 2~4시간

### P0-4. ρ(H) 측정 + 안전장치 작동 보고

- **현황**: 5 firms 합성에서는 ρ=0.00002 (사이클 없음). 실 1만+ 그래프에서 ρ 가 어디인지 미상
- **시작 트리거**: H 행렬 빌드 직후
- **의존**: matrix_H.build(edges, firms, year) 1회 실행
- **결정 항목**: ρ < 0.95 → 정상 / ρ ≥ 0.95 → row_normalize 자동 + 사용자에게 보고
- **산출물**: 시뮬 1회 보고서에 ρ(H) 값 + capped_ratio 포함 (그래프모델 v2.1 §8.3.4 H-4 쿼리)
- **예상 시간**: 30분 (자동화됨 — 단지 결과 해석)

---

## P1 — 5주차 (~1주)

### P1-1. `matrix/matrix_R.py` — R 행렬 (산업 IO Top-down 분해)

- **시작 트리거**: P0 완료
- **의존**: 한국은행 IO 33×33 행렬 + 산업 코드 매핑
- **결정 항목**: A_io 데이터 소스 (한국은행 ECOS / 자체 정제)
- **산출물**: `matrix_R.build(firms, A_io, io_codes) -> RData`, `solve_with_R(delta_y_firm, rd) -> delta_x_firm`
- **단위 테스트**: 33×33 산업 IO 결과 재현 (test_consistency.py — KDT 보고서 §III.3)
- **예상 시간**: 2~3일

### P1-2. `matrix/matrix_B.py` — α 슬라이더 민감도

- **시작 트리거**: P1-1 완료
- **의존**: H + R 모두 빌드 가능
- **산출물**: `BMatrixOperator(H, R.Rx, alpha)`, `sensitivity_scan(H, Rx, dy, alphas=[0,0.3,0.5,0.7,1])`, `range_ratio` / `cv`
- **단위 테스트**: α=0/0.5/1 산업 총량 보존
- **예상 시간**: 1~2일

---

## P2 — 6주차 (~1주)

### P2-1. `propagation/shortest_path.py` — 한주동 부도 전이 BFS

- **시작 트리거**: P1 완료
- **의존**: CRI 등급 (firms.cri_score), 거래 distance = 1 - PD
- **산출물**: PD 로짓 회귀 (법인 / 개인 사업자 분기, 구현명세서 §4.3) + BFS depth ≤ 3 + LOESS lookup table
- **단위 테스트**: 부도 firm set → BFS 결과 (target, depth) 그룹 내 최단 distance
- **예상 시간**: 2~3일

### P2-2. `indicator/network_cri.py` — 판매망/구매망 CRI 가중

- **시작 트리거**: P2-1 후 또는 병렬
- **의존**: cri_score, sales_weight, purchase_weight
- **산출물**: 무한등비급수형 `S = (1-λ) × W × (I - λW)^(-1) × r` + cutoff 형 (α_1·Wr + α_2·W²r + …)
- **단위 테스트**: S ∈ [1, 10] 보존, cutoff vs 무한급수 비교 (구현명세서 §9.1)
- **예상 시간**: 1~2일

---

## P3 — 7주차 (~1주)

### P3-1. `estimate/sales_estimator.py` (LightGBM)

- **시작 트리거**: P2 완료
- **의존**: VAT 추정매출 + GDP 성장률 + 재무제표 매출 + 수출 추정 + KSIC 산업 평균
- **산출물**: LightGBM 모델 + 10분위 calibration (구현명세서 §7.2)
- **성능 목표**: RMSLE 0.81, R² 0.80
- **`extras=ml`** 활성화 필요 (`pip install -e ".[ml]"`)
- **예상 시간**: 3~4일

### P3-2. `estimate/asset_estimator.py` + `export_estimator.py`

- 동일 패턴. 자산: RMSLE 0.44 R² 0.66, 수출: 로그정규분포 + 분위 calibration
- **예상 시간**: 2~3일

---

## P4 — 8주차 (시연 직전)

### P4-1. 통합 검증 (구현명세서 §9.2)

- KDT 철강 관세 25% 시나리오 재현 → 산업별 Δx 비교
- BiCGSTAB / Sparse LU / 직접 역행렬 결과 일치 확인 (소규모)
- 허브 노드(예: 삼성전자, POSCO) 1차 충격 시 발산 없음
- α=0/0.5/1 민감도 산업 총량 보존
- MIXED 시나리오 각 Run 합 = 그룹 합 정합
- **예상 시간**: 2~3일

### P4-2. 부하 테스트

- 1만 노드 × 8M 엣지 시뮬 1회 시간 측정 (목표: 안전장치 ON 상태에서 < 1분, 화면 첫 로딩 < 2초)
- 화면 ①~⑦ 응답시간 (구현명세서 §7.1 SLO 표 검증)
- **예상 시간**: 2일

### P4-3. 시연 자료 + 한계점 정리

- 시연 시나리오 3종 (관세 / B2C / 수입 가격) 사전 적재
- Phase 2 제안서 (λ 추정 / 부도 전이 확률 모형 / hub-aware BFS 등)
- **예상 시간**: 2일

---

## P5 — PoC 2차 (캐시 계층 도입)

### P5-1. `cache/` 모듈 신설

- 아키텍처 §5.2 — 6개 키 종류 (kpi, layout, firm, typeahead, mv_sector, timeseries, rho, session)
- TTL 정책 (§4.3)
- 무효화 이벤트 (시뮬 종료 / firms 마스터 변경 / ETL 갱신)
- **의존**: PoC 1차 시연 종료 + 사용자 동의
- **산출물**:
  - `cache/client.py` (캐시 pool + key prefix; 기술 TBD — redis 제거됨)
  - `cache/kpi.py` / `layout.py` / `firm.py` / `timeseries.py` / `invalidate.py`
  - `dual_write` 에 캐시 warmup 단계 추가
- **예상 시간**: 1주

### P5-2. FastAPI 라우터 본 구현 (501 → 실제)

- 화면 ①~⑦ 의 GET 엔드포인트 실 데이터 응답
- KPI 카드는 캐시 우선, PG MV 폴백
- 좌표 캐시 적용 (force-directed 사전 계산)
- **예상 시간**: 1주

---

## P6 — 운영 1.0 (`sync/` 도입)

### P6-1. `sync/` 모듈 신설

- 아키텍처 §5.4 — `neo4j_to_pg.write_impacts_dual` 의 운영 1.0 버전 (현재 `result/dual_write` 가 PoC 1차 형태)
- `firms_master.sync_firms_master()` — PG ↔ Neo4j 마스터 정합성 보장 (일 1회 cron 또는 Airflow DAG)
- `mv_refresh.refresh_all_views(run_id)` — 트리거 기반
- `cache_warmup.warmup_after_run(run_id)` — 시뮬 종료 시 캐시 사전 적재
- **결정 항목**: `result/dual_write` 를 `sync/` 로 이동할지 vs 유지할지 (ADR-006 작성 대상)
- **예상 시간**: 1주

### P6-2. Alembic revision 1차 (의도된 schema 변경)

- 예: `ui_severity` enum CHECK 제약 추가, `simulation_runs.scenario_name` 비정규화 등
- **의존**: 운영 중 발생한 실제 schema 변경 요구
- **산출물**: `alembic/versions/0002_*.py` + upgrade head 적용
- **예상 시간**: 변경마다 30분~2시간

---

## P7 — 운영 1.1 (검색 + 임베딩)

### P7-1. `search/` 모듈 신설

- `search/typeahead.py` (pg_trgm 자동완성 < 50ms)
- `search/fulltext.py` (mecab-ko + tsvector)
- `search/semantic.py` (pgvector HNSW + sentence-transformers)
- `search/hybrid.py` (RRF 가중 ranking)
- **의존**:
  - `mecab_ko` PG 확장 빌드 (별도 Docker 이미지 또는 PG 컴파일)
  - `extras=search` 활성화 (`sentence-transformers`, `torch`)
- **예상 시간**: 1~2주

### P7-2. `search/embedding.py` — 배치 임베딩

- firm_name 임베딩 일괄 계산 → `firms.firm_name_embedding` (vector(384))
- firm_name 변경 시 재계산 트리거
- **메모리**: 1만 firms × 384 × float32 ≈ 15MB. 40만 = 600MB
- **예상 시간**: 3일

---

## 잡일 / 기술 부채 (언제든 끼워넣기 가능)

| 항목 | 위치 | 시간 | 가치 |
|---|---|---|---|
| `to_neo4j.write_impacts` 반환을 TypedDict 화 | result/to_neo4j.py | 30분 | mypy cast 제거 |
| `affected_firms` 가 `revenue_sum != 0` → `abs > epsilon` | result/aggregate.py | 20분 | 부동소수 노이즈 제거 |
| GDP/GOV_REVENUE base 컬럼 분리 (현재는 sales 폴백) | shock/direct_shock.py | 1시간 | GOV 정확도 ↑ |
| Supply 비용 base 의 vat_fs_est_purchase 외 폴백 | shock/direct_shock.py | 1시간 | SUPPLY 정확도 ↑ |
| `impacts.capped` 를 `revenue_capped / cost_capped` 분리 | DDL + dual_write | 2시간 | UI 표시 정확 |
| Alembic `0002_*` 로 schema 변경 추적 시작 | alembic/versions/ | per change | 운영 표준 |
| CI 에 mypy 추가 | .github/workflows/ci.yml | 10분 | 회귀 방지 강화 |
| FastAPI `from_` query alias 같은 PEP 8 충돌 패턴 통일 | api/routers/firms.py | 30분 | 일관성 |
| 1만 firm 임계점 측정 — `apoc.periodic.iterate` 도입 시점 | result/to_neo4j.py | 2시간 | 운영 확장성 |

---

## 추적 방식 권장

- 본 문서의 각 P 항목은 **GitHub Issue 1개씩** 으로 옮기는 게 정석. Issue 본문에 시작 트리거 / 의존 / 산출물을 그대로 복사.
- Issue 진행도가 PR 머지 SHA 와 연결되면 `PROGRESS.md` 의 다음 commit 자동 갱신.
- `ROADMAP` 의 큰 변경(예: `cache/` 신설 시점 결정)은 ADR 추가 (`ARCHITECTURE_DECISIONS.md` ADR-006~).
