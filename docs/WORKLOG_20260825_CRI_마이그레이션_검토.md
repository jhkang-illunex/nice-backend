# 작업 기록 — CRI 마이그레이션 및 shock API 정비 (2026-08-24 ~ 08-25)

> 검토 회의용 기록. 작성 2026-08-25. 담당: jhkang.
> 핵심 검토 안건은 [§5 검토·결정 필요 사항](#5-검토결정-필요-사항).

---

## 1. CRI 마이그레이션 루틴 신설 (PR #42) — 오늘의 주 작업

### 1.1 무엇을 만들었나

기존 모듈 조합으로 "rate 갱신 → cri2 점수 산출 → 등급 테이블 기록"을 한 명령으로:

```bash
python -m nice_migrate --env-file .env --cri --year 2024 [--dry-run]
```

| 단계 | 모듈 | 입력 → 출력 |
| --- | --- | --- |
| ① rate 갱신 | `nice_migrate.rate.update_trade_rate` (기존) | company_edge 연도별 trade_rate·sell_rate·buy_rate 재계산 |
| ② cri2 점수 | `nice_migrate.cri` (신규) + `nice_ingest.pipelines.cri.pipeline` core | S[판매][구매]=sell_rate, P[구매][판매]=buy_rate 행렬 → 누적망 T=W+W²+… → 등급 가중평균 |
| ③ 기록 | 〃 | `company_credit_cri(bizno, grd_st_year)`.weight_sell_avg / weight_buy_avg |

- 알고리즘은 **cri2 원본(NICE 샘플) 이관 구현 그대로** — CSV 경로와 DB 경로가
  `cumulative_scores()` 공용 함수를 공유(중복 구현 없음).
- 연도 매칭: `company_edge.trade_year == company_credit_cri.grd_st_year` 교집합만 처리.
- 무등급(NR 등)·미산출은 cri2 규칙대로 NULL. 등급 행이 없는 노드는 기록 생략(통계 보고).
- 테스트 4건(cri2 스펙 5노드 샘플 값 일치 포함), 에어갭 migrate 이미지에
  `src/nice_ingest` COPY 추가(순수 stdlib — 의존 추가 없음).

### 1.2 2024년 실제 적용 결과 (운영 PG 172.30.1.101, 2026-08-25)

**① rate**: 62행 갱신. 검산 전부 통과 — Σ_out=1.0 정확, sell_rate≡trade_rate,
buy_rate 전행 `basis='target_purchases'`(매입총액 기준), 평균 0.984.

**② 점수 기록 — 2행만 갱신됨 (아래 §1.3 원인)**:

| bizno | 등급 | weight_sell_avg | weight_buy_avg |
| --- | --- | --- | --- |
| 1018116406 | AA+ | NULL | **2.0** |
| 3018702315 | AA+ | **2.0** | NULL |

다른 연도 오염 없음(2022·2023·2025 무변경) 확인.

### 1.3 결과가 희소한 원인 — 알고리즘이 아니라 데이터 교집합

- 2024 등급 행 183개 중 **2024 거래망(63노드)에 존재하는 기업은 2개뿐**.
  나머지 181개 등급 기업은 company_edge에 등장하지 않음(거래망 위치 없음 → 점수 개념 불성립).
  역으로 거래망 63노드 중 61개는 등급 테이블에 행이 없어 계산돼도 기록할 곳 없음.
- **sell/buy 둘 다 채워진 회사 0개인 이유**: 유효(등급 보유) 상대가 서로 둘뿐인데
  둘 사이 거래가 `3018702315 → 1018116406` **한 방향 1건**뿐.
  - 3018702315: 구매(to) 기록 0건 → 구매망 자체가 빔 → buy NULL
  - 1018116406: 판매 25건의 구매자 전부 무등급 → sell NULL
- **정합성 근거**: 유효 상대가 정확히 1개(AA+=2점)이므로 가중평균은 반드시 2.0 — 실측 일치.

### 1.4 2025년 (조회만, 미적용)

구조가 2024와 판박이: 등급 178행 / 거래망 62엣지·63노드 / 교집합 **같은 두 회사** /
같은 한 방향 거래(19.8억). 적용 시 결과도 동일 형태 예상. 2025 rate는 아직 미갱신 상태.

### 1.5 DB 데이터 실측 사실 (검토 시 공유)

- company_edge 데이터가 교체돼 있음: **trade_year 2022~2025 각 62행, 총 248행**
  (과거 "2024·2026만" 기록은 무효). 적용 전 rate 컬럼은 전부 NULL이었음.
- company_credit_cri: 512행, (bizno, grd_st_year) 유일, grd_st_year 2024/2025/2026.
  weight_* 컬럼은 기존 전부 NULL이었음(이번이 최초 기록).
- buy_rate 정의(중요): 커밋 0268794 이후 **기본이 매입총액(Σ_in) 기준** —
  `--buy-fallback`은 사실상 no-op(하위호환 보존). dry-run preview의
  `buy_rate_fallback_would_update`(240행)는 옛 정의 기준 카운트라 오해 소지(§5-4).

---

## 2. shock API 정비 (같은 기간)

| PR | 내용 |
| --- | --- |
| #38 | `/api/cri` 외부 비노출 — 라우트 데코레이터만 주석(코드·스키마 보존, 재노출=주석 해제). 호출 시 404 |
| #40 | tariff·volume에 `iokind`(in/out, 기본 in) 추가 — **rate 조회 방향**(tseximdivcd 3/0)을 `direction`(전파 방향)에서 분리. volume은 인자 통일용 예약(미사용) |
| #41 | backend `/trade/weight` 주소 `.env` 주입(`RATE_API_URL`/`RATE_API_TIMEOUT`) + **배포 compose에 배선 누락 버그 수정**(기존엔 배포 시 tariff 무조건 503) |
| #35 | shock-server 이미지 httpx 누락 수정(rate_client 기동 불가 crash-loop 해소) |

검증: 테스트 27건, 운영 컨테이너 실호출로 iokind 방향별 금액 대조
(out=752,923 / in=1,700,574 = 독립 계산과 일치), 컨테이너 재배포 완료.

## 3. API 문서 (postman) 정비

| PR | 내용 |
| --- | --- |
| #34·#37·#39 | `docs/api/shock.postman_collection.json` — 설명·실측 예시 응답 8건(422·503 포함)·pm.test(total_shock=Σshock 항등식)·CRI 제외. 환경 파일 2벌(local 8004/deploy 18004) |
| #36 | `docs/api/rag.postman_collection.json` — 7개 엔드포인트 설명·실측 예시(전부 200) |

rag 서버 스모크: `/health/deep`(pg·llm·embed ok)·hsk/ksic search·agent·통합검색 전부 정상.

## 4. 배포 상태

- main = 전 PR 병합 완료. 로컬 컨테이너(shock/rag/rate-mock)는 최신 main 기준 재배포·검증 끝.
- **에어갭 반입 시 이미지 tar 외 필수 2가지**: ① 갱신된 `docker-compose.deploy.yml`
  (RATE_API_URL 배선), ② 현장 `.env`에 `RATE_API_URL=http://<backend>/trade/weight` 추가.
  ingestion 이미지는 CRI CSV 파이프라인 반영 시 재빌드 필요. (상세: RUNBOOK_설치.md)

---

## 5. 검토·결정 필요 사항 (회의 안건)

1. **등급 커버리지**: 거래망 63개 bizno 중 등급 보유 2개 → 점수가 사실상 두 회사에만 나옴.
   거래망 참여 기업의 등급을 company_credit_cri에 적재 요청할지? (무등급 61개 목록 추출 가능)
2. **2025년 적용 여부**: 구조 동일 확인됨. 적용은 명령 1회(`--cri --year 2025`).
3. **buy_rate 정의 공유**: 현행 기본 = 매입총액(Σ_in) 기준(0268794). cri2 원본의
   "구매자 거래총금액" 해석과의 정합 확인 필요 시 원본 샘플과 수치 대조 가능.
4. **fallback preview 드리프트**: dry-run의 240행 표시는 옛 정의 기준 — preview 수정
   또는 `--buy-fallback` 옵션 제거로 정리할지.
5. **volume의 iokind 사용 여부**: 현재 예약(미사용) — DB 구조 검토 후 확정 예정이라 했던 건.

## 부록 — 검증용 쿼리

```sql
-- 기록된 점수 확인
SELECT trim(bizno), crigrd, weight_sell_avg, weight_buy_avg
FROM company_credit_cri WHERE grd_st_year='2024'
  AND (weight_sell_avg IS NOT NULL OR weight_buy_avg IS NOT NULL);

-- 등급 ∩ 거래망 교집합 (연도 치환)
SELECT count(*) FROM company_credit_cri g
WHERE g.grd_st_year='2024' AND trim(g.bizno) IN (
  SELECT trim(from_bizno) FROM company_edge WHERE trade_year='2024'
  UNION SELECT trim(to_bizno) FROM company_edge WHERE trade_year='2024');

-- rate 검산
SELECT trade_year, count(*), count(sell_rate), round(avg(buy_rate)::numeric,4)
FROM company_edge GROUP BY 1 ORDER BY 1;
```
