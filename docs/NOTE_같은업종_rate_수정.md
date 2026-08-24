# company_edge rate 계산 — "같은 업종 기업 간 거래만" 수정 노트

> 에어갭 환경에서 이 문서를 **보면서 손으로** `rate.py`(또는 운영 SQL)를 고치기 위한 참고.
> 데이터는 기업↔기업 **매출(`sly_amt`) 하나뿐**. `from`/`to` 업종이 **같은 거래만** 골라
> `trade_rate` / `sell_rate` / `buy_rate` 를 다시 계산한다.
>
> **안 A**(분모도 같은 업종) / **안 B**(분모는 전체) 두 경우를 각각 완결된 세트로 제공.

---

## 0. 먼저 확인 (에어갭 DB에서 직접)

내가 접속하는 DB와 발주처 DB가 **다르므로**, 업종 컬럼 실제 이름을 먼저 확인한다.
아래는 인덱스명에서 추정한 `from_ksic_mid` / `to_ksic_mid`(중분류). 실제 스키마로 교체할 것.

```sql
-- 업종 관련 컬럼 확인
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema='public' AND table_name='company_edge'
  AND column_name ILIKE '%ksic%'
ORDER BY column_name;

-- 필터 후 몇 행이 남는지 미리 파악
SELECT
  COUNT(*)                                              AS 전체,
  COUNT(*) FILTER (WHERE from_ksic_mid IS NULL
                      OR to_ksic_mid IS NULL)           AS 업종NULL,
  COUNT(*) FILTER (WHERE from_ksic_mid = to_ksic_mid)   AS 같은업종,
  COUNT(*) FILTER (WHERE from_ksic_mid <> to_ksic_mid)  AS 다른업종
FROM public.company_edge;
```

- `from_ksic_mid = to_ksic_mid` 는 **한쪽이라도 NULL 이면 NULL → 제외**된다(업종 미상 거래 탈락).
  포함하려면 `COALESCE(from_ksic_mid,'') = COALESCE(to_ksic_mid,'')` 등으로 정책 결정.
- 업종 단위(대/중/세분류)에 따라 `_mid` 가 아닐 수 있음. 원하는 단위 컬럼으로 지정.

---

## 1. 두 안의 차이 (한 줄 요약)

| | **안 A** — 같은 업종 내부 정규화 | **안 B** — 전체 대비 같은 업종 몫 |
|---|---|---|
| 분모 집계(내부 서브쿼리) | 같은 업종 거래만 합산 | **전체**(타 업종 포함) 합산 |
| 계산·기록 대상 행 | 같은 업종 거래 | 같은 업종 거래 |
| source 별 rate 합 | **≈ 1** | **< 1** (타 업종으로 나간 몫 제외) |
| 의미 | "같은 업종 안에서의 상대 비중" | "전체 매출 대비 같은 업종 비중" |

차이는 **내부 집계 서브쿼리의 `WHERE` 에 업종조건을 넣느냐(A) / 빼느냐(B)** 뿐.
외부 UPDATE 대상 제한(`e.from_ksic_mid = e.to_ksic_mid`)은 **두 안 공통**.

---

## 2. `rate.py` 수정 — SQL 상수는 그대로, `where` 만든 두 줄만 교체

세 UPDATE(`_UPDATE_SQL`, `_UPDATE_SELL_SQL`, `_UPDATE_BUY_SQL`)가 모두 `{where}`(내부 집계)와
`{where_e}`(외부 조인)를 공유한다. **SQL 상수 3개는 건드리지 않는다.**
`update_trade_rate()` 안(현재 225-227행)의 `where`/`where_e` 조립부만 고친다.

### AS-IS (현재 225-227행)

```python
    where = "WHERE CAST(trade_year AS text) = :year" if year else ""
    where_e = "AND CAST(e.trade_year AS text) = :year" if year else ""
    params = {"year": str(year)} if year else {}
```

### TO-BE — 안 A (분모도 같은 업종)

```python
    same_ksic_only = True  # 필요 시 함수 인자/CLI 플래그로 승격
    conds, conds_e = [], []
    if year:
        conds.append("CAST(trade_year AS text) = :year")
        conds_e.append("CAST(e.trade_year AS text) = :year")
    if same_ksic_only:
        conds.append("from_ksic_mid = to_ksic_mid")        # ★ 내부 집계(분모)에도 적용 = 안 A
        conds_e.append("e.from_ksic_mid = e.to_ksic_mid")   # 외부 대상 행 제한(공통)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    where_e = ("AND " + " AND ".join(conds_e)) if conds_e else ""
    params = {"year": str(year)} if year else {}
```

### TO-BE — 안 B (분모는 전체, 대상만 제한)

안 A 와 **딱 한 줄 차이**: `conds.append(...)`(내부 집계)를 **빼고**, `conds_e.append(...)`만 남긴다.

```python
    same_ksic_only = True
    conds, conds_e = [], []
    if year:
        conds.append("CAST(trade_year AS text) = :year")
        conds_e.append("CAST(e.trade_year AS text) = :year")
    if same_ksic_only:
        # (안 B) 내부 집계에는 업종조건 넣지 않음 → 분모는 전체 매출
        conds_e.append("e.from_ksic_mid = e.to_ksic_mid")   # 대상 행만 같은 업종
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    where_e = ("AND " + " AND ".join(conds_e)) if conds_e else ""
    params = {"year": str(year)} if year else {}
```

> 이 변경 한 번으로 trade_rate/sell_rate/buy_rate + 검산(`_VERIFY_SQL`/`_VERIFY_SHARES_SQL`)이
> 같은 스코프로 자동 정합된다.
> **전체 계산과 병행**하려면 상수 대신 CLI 플래그로:
> `argparse` 에 `--same-ksic-only` 추가 → `update_trade_rate(..., same_ksic_only=args.same_ksic_only)`,
> 함수 시그니처에 `same_ksic_only: bool = False` 추가.

---

## 3. 안 A — 직접 실행 SQL (분모도 같은 업종, year=2026 예)

CLI 안 거치고 DB에서 바로 돌릴 형태. `from_ksic_mid`/`to_ksic_mid` 는 실제 컬럼명으로.
`buy_rate_basis` 컬럼 없으면 먼저:
`ALTER TABLE public.company_edge ADD COLUMN IF NOT EXISTS buy_rate_basis text;`

### A-1. trade_rate = sly_amt / Σ_out(from), 같은 업종 내부

```sql
UPDATE public.company_edge e
SET trade_rate = CASE WHEN t.tot > 0 THEN e.sly_amt / t.tot ELSE 0 END
FROM (
    SELECT from_bizno, trade_year, SUM(sly_amt)::numeric AS tot
    FROM public.company_edge
    WHERE CAST(trade_year AS text) = '2026'
      AND from_ksic_mid = to_ksic_mid          -- ★ 분모도 같은 업종만
    GROUP BY from_bizno, trade_year
) t
WHERE e.from_bizno = t.from_bizno AND e.trade_year = t.trade_year
  AND CAST(e.trade_year AS text) = '2026'
  AND e.from_ksic_mid = e.to_ksic_mid;         -- 대상 행 제한
```

### A-2. sell_rate(= trade_rate 공식) + buy_rate baseline 0

```sql
UPDATE public.company_edge e
SET sell_rate = CASE WHEN t.tot > 0 THEN e.sly_amt / t.tot ELSE 0 END,
    buy_rate = 0,
    buy_rate_basis = NULL
FROM (
    SELECT from_bizno, trade_year, SUM(sly_amt)::numeric AS tot
    FROM public.company_edge
    WHERE CAST(trade_year AS text) = '2026'
      AND from_ksic_mid = to_ksic_mid          -- ★
    GROUP BY from_bizno, trade_year
) t
WHERE e.from_bizno = t.from_bizno AND e.trade_year = t.trade_year
  AND CAST(e.trade_year AS text) = '2026'
  AND e.from_ksic_mid = e.to_ksic_mid;
```

### A-3. buy_rate = sly_amt / Σ_in(to), 같은 업종 매입총액

```sql
UPDATE public.company_edge e
SET buy_rate = e.sly_amt / t.tot,
    buy_rate_basis = 'target_purchases'
FROM (
    SELECT to_bizno, trade_year, SUM(sly_amt)::numeric AS tot
    FROM public.company_edge
    WHERE CAST(trade_year AS text) = '2026'
      AND from_ksic_mid = to_ksic_mid          -- ★
    GROUP BY to_bizno, trade_year
) t
WHERE e.to_bizno = t.to_bizno AND e.trade_year = t.trade_year AND t.tot > 0
  AND CAST(e.trade_year AS text) = '2026'
  AND e.from_ksic_mid = e.to_ksic_mid;
```

> 실행 순서: A-2 → A-3 (A-2 가 buy_rate 를 0 으로 깔고, A-3 가 채움). A-1 은 독립.

---

## 4. 안 B — 직접 실행 SQL (분모는 전체, year=2026 예)

**안 A 와의 유일한 차이**: 내부 집계 서브쿼리의 `AND from_ksic_mid = to_ksic_mid` **한 줄 제거**.
외부 `AND e.from_ksic_mid = e.to_ksic_mid` 는 **유지**.

### B-1. trade_rate = sly_amt / Σ_out(from), 분모는 전체 매출

```sql
UPDATE public.company_edge e
SET trade_rate = CASE WHEN t.tot > 0 THEN e.sly_amt / t.tot ELSE 0 END
FROM (
    SELECT from_bizno, trade_year, SUM(sly_amt)::numeric AS tot
    FROM public.company_edge
    WHERE CAST(trade_year AS text) = '2026'
    -- (안 B) 업종조건 없음 → 분모 = from 의 전체 매출
    GROUP BY from_bizno, trade_year
) t
WHERE e.from_bizno = t.from_bizno AND e.trade_year = t.trade_year
  AND CAST(e.trade_year AS text) = '2026'
  AND e.from_ksic_mid = e.to_ksic_mid;         -- ★ 대상만 같은 업종
```

### B-2. sell_rate + buy_rate baseline 0

```sql
UPDATE public.company_edge e
SET sell_rate = CASE WHEN t.tot > 0 THEN e.sly_amt / t.tot ELSE 0 END,
    buy_rate = 0,
    buy_rate_basis = NULL
FROM (
    SELECT from_bizno, trade_year, SUM(sly_amt)::numeric AS tot
    FROM public.company_edge
    WHERE CAST(trade_year AS text) = '2026'
    -- (안 B) 업종조건 없음
    GROUP BY from_bizno, trade_year
) t
WHERE e.from_bizno = t.from_bizno AND e.trade_year = t.trade_year
  AND CAST(e.trade_year AS text) = '2026'
  AND e.from_ksic_mid = e.to_ksic_mid;
```

### B-3. buy_rate = sly_amt / Σ_in(to), 분모는 to 의 전체 매입총액

```sql
UPDATE public.company_edge e
SET buy_rate = e.sly_amt / t.tot,
    buy_rate_basis = 'target_purchases'
FROM (
    SELECT to_bizno, trade_year, SUM(sly_amt)::numeric AS tot
    FROM public.company_edge
    WHERE CAST(trade_year AS text) = '2026'
    -- (안 B) 업종조건 없음 → 분모 = to 의 전체 매입총액
    GROUP BY to_bizno, trade_year
) t
WHERE e.to_bizno = t.to_bizno AND e.trade_year = t.trade_year AND t.tot > 0
  AND CAST(e.trade_year AS text) = '2026'
  AND e.from_ksic_mid = e.to_ksic_mid;
```

> 안 B 는 분모(전체 매출/매입)에 타 업종 거래가 포함되므로 `rate` 합 < 1, 개별 `rate` 는 여전히 [0,1].

---

## 5. 검증 쿼리 (year=2026 예)

```sql
-- source 별 rate 합: 안 A ≈ 1, 안 B < 1
SELECT MIN(s), MAX(s), AVG(s) FROM (
    SELECT SUM(trade_rate)::float s
    FROM public.company_edge
    WHERE CAST(trade_year AS text)='2026' AND from_ksic_mid = to_ksic_mid
    GROUP BY from_bizno, trade_year
    HAVING SUM(sly_amt) > 0
) q;

-- buy_rate 범위·근거 분포 (두 안 모두 [0,1] 이어야 정상)
SELECT MIN(buy_rate), MAX(buy_rate), AVG(buy_rate),
       COUNT(*) FILTER (WHERE buy_rate_basis IS NULL)               AS baseline0,
       COUNT(*) FILTER (WHERE buy_rate_basis = 'target_purchases')  AS 계산됨
FROM public.company_edge
WHERE CAST(trade_year AS text)='2026' AND from_ksic_mid = to_ksic_mid;

-- 1 초과가 남으면 오류 (0 이어야 정상)
SELECT COUNT(*) FROM public.company_edge
WHERE (trade_rate > 1.0000001 OR buy_rate > 1.0000001 OR sell_rate > 1.0000001)
  AND from_ksic_mid = to_ksic_mid;
```

---

## 6. 성능 (선택)

`from_ksic_mid = to_ksic_mid` 는 컬럼 대 컬럼 비교라 일반 인덱스로 못 좁힌다(스캔 후 필터).
행 수가 크면 같은-업종 **부분 인덱스**가 도움:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ce_year_from_sameksic
  ON public.company_edge (trade_year, from_bizno)
  WHERE from_ksic_mid = to_ksic_mid;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ce_year_to_sameksic
  ON public.company_edge (trade_year, to_bizno)
  WHERE from_ksic_mid = to_ksic_mid;
```

> `CAST(trade_year AS text)='2026'` 는 표현식이라 `trade_year` 인덱스를 못 탄다.
> `trade_year` 가 문자형이면 캐스트를 빼고 `trade_year = '2026'` 로 두면 인덱스 사용 가능.
