# 아키텍처 결정 기록 (ADR)

본 문서는 `docs/` 의 3개 설계서(폴리글랏 아키텍처 v1.0, Neo4j 그래프모델 v2.3,
Python 구현명세서 v1.1) 를 종합하여 PoC 1차 부트스트랩 시점에 확정된
결정만 기록한다. 산식·스키마는 원본 설계서를 참조한다.

---

## ADR-001. Neo4j 토폴로지: 프로젝트 전용 신규 싱글 노드 (Community 5.x)

### 결정
프로젝트 디렉토리 내 `docker-compose.yml` 로 Neo4j 5.x Community Edition
싱글 노드를 띄운다. 기존 서버의 Enterprise 5.x 클러스터는 사용하지 않는다.

### 근거
1. **격리** — PoC 동안 `:Scenario / :Shock / :SimulationRun / :IMPACTS`
   등의 노드가 사용자 입력에 따라 폭증/삭제된다. 운영 클러스터와 섞일
   이유가 없다.
2. **에디션 회피** — 클러스터 사용 시 named database 권한 협의, 라이선스
   시트, APOC 일관 설치 등 운영 부담이 발생한다.
3. **워크로드 부적합** — 본 PoC 의 쓰기는 `apoc.periodic.commit` 의 잦은
   소량 트랜잭션. Causal Cluster 의 quorum write 가 주는 이점이 없다.
4. **계산 부하의 본체는 Python (`scipy.sparse.linalg`)** — Neo4j 는 그래프
   traversal 과 결과 누적 저장소 역할만 한다. HA 요건이 없다.
5. **백업/이관 단순** — `neo4j-admin database dump` 한 줄로 시연 직전
   스냅샷이 가능하다.

### 사양 (설계서 §8.2 권장값)
- RAM 16GB, 페이지 캐시 8GB, heap 4GB, SSD
- APOC core + (필요 시) APOC Extended 동봉

### 재검토 트리거
- 노드 40만 + 동시 사용자 ≥ 10 도달
- 다중 환경 (개발/스테이징) 분리 필요
- 운영 1.0 단계에서 PG 가 source-of-truth 가 된 뒤 read replica 요구
이 중 하나라도 발생하면 ADR-001 을 다시 연다.

---

## ADR-002. PostgreSQL: 프로젝트 전용 신규 (PG 16 + pgvector)

### 결정
프로젝트 컨테이너로 `pgvector/pgvector:pg16` 이미지를 사용한다. 확장은
초기에 `vector / pg_trgm / btree_gin` 만 활성화한다.

### 보류 항목
- `mecab_ko` 한국어 형태소 확장은 별도 빌드가 필요. 설계서 §9.3 위험표
  대응안 (초기: pg_trgm + tsvector 영문화 / 후기: mecab-ko 도입) 을
  따른다. 운영 1.1 단계에서 다시 결정한다.
- `sentence-transformers` 임베딩은 운영 1.1 단계에서 도입. 부트스트랩
  의존성에서는 제외한다.

---

## ADR-003. Python 패키지 명: `nice_poc`

Python 구현명세서 §1.3 의 패키지명을 그대로 사용한다. PoC 1차 디렉토리는
설계서 §7.3 phase matrix 의 1차 컬럼을 따른다.

```
src/nice_poc/
├── data/         # Neo4j -> DataFrame + scipy.sparse 추출
├── matrix/       # 5종 투입계수 행렬 (A, A1, H, R, B)
├── shock/        # 8종 시나리오 Δy 계산
├── propagation/  # BiCGSTAB / Sparse LU / BFS
├── indicator/    # Edge Value / TIS / CRI 가중
├── safety/       # ρ(A) 체크 / row-normalize / cap / 허브
├── estimate/     # ML 추정 (보조)
├── result/       # :IMPACTS 적재 + 화면 집계
├── config/       # pydantic-settings (env 로딩)
├── db/           # Neo4j / PG / Redis 클라이언트
└── api/          # FastAPI 진입점
```

`cache/`(PoC 2차), `sync/`(운영 1.0), `search/`(운영 1.1) 은 본 1차에서
**디렉토리 자체를 만들지 않는다.** Phase 진입 시 신설한다.

---

## ADR-004. PostgreSQL DDL 적재 방식

초기 부트스트랩은 `deploy/postgres/init/*.sql` 을 docker entrypoint
`/docker-entrypoint-initdb.d/` 에 마운트하여 컨테이너 최초 기동 시
일괄 적용한다. 이후 스키마 변경은 Alembic 으로 관리한다.

이유: Alembic 의 첫 마이그레이션을 만들기 전 단계에 빠르게 시연
가능한 형상을 확보하기 위함. 적재 순서 의존(impacts → firms FK 등)을
파일 prefix 로 강제한다.

---

## ADR-005. 의존성 정책

- 런타임 의존성은 `pyproject.toml` 의 기본 deps 에 둔다.
- ML(`lightgbm`, `scikit-learn`) 과 임베딩(`sentence-transformers`,
  `torch`) 은 **extras** 로 분리한다. PoC 1차 본체 컨테이너가 커지지
  않도록.
- Phase 별 모듈 추가 시 extras 도 함께 추가한다.

```
pip install -e .              # PoC 1차 본체
pip install -e ".[ml]"        # estimate/ 활성화 시
pip install -e ".[search]"    # 운영 1.1 진입 시
pip install -e ".[dev]"       # 테스트/린트
```
