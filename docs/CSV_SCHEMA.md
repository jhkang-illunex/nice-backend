# CSV 스키마 참조

`docs/` 의 설계서 3종을 따라 도메인 ETL 파이프라인이 기대하는 CSV 컬럼을
한 곳에 모았다. CSV 가 본 컬럼명과 일치하면 `python -m nice_poc.etl ...` 도메인
서브커맨드로 바로 적재 가능하다. 다르면 `upload-pg / upload-neo4j` 의
`--rename` 으로 매핑하거나 사전 정리한다.

문자열 컬럼은 `text` / `varchar` / 그래프 string, 숫자 컬럼은 PG `NUMERIC` 또는
Neo4j `Float`/`Integer`. 빈 값은 빈 문자열 또는 NA — pandas 가 NaN 으로 읽어
PG `NULL` / Neo4j `null` 로 저장된다.

---

## 디렉토리 컨벤션 (도메인 파이프라인)

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

---

## firms.csv

PG: `firms` 테이블 + Neo4j: `:Firm` 노드 + `:IN_SECTOR / :BELONGS_TO / :OBSERVED_IN / :LOCATED_IN` 관계.

| 컬럼 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `firm_id` | string(6) | ★ | UPCHECD (PK) |
| `biz_no` | string(13) | ★ | 사업자번호 |
| `rep_bizno` | string(13) |  | 본점 사업자번호 (Headquarter 노드 키) |
| `firm_name` | text | ★ | 기업명 |
| `sector_code` | string(10) |  | KSIC 5자리 (Sector FK) |
| `firm_data_type` | string(30) |  | VAT_ONLY/VAT_FULL 등 |
| `firm_confidence_level` | string(10) |  | HIGH/MEDIUM/LOW |
| `base_year` | int |  | 결산연도 (Year 노드 키) |
| `sales_year_fin` | numeric |  | 재무 매출 (행렬 분모 1순위) |
| `sales_year_vat_observed` | numeric |  | 부가세 기준 매출 |
| `vat_fs_est_sales` | numeric |  | 추정 매출 (행렬 분모 2순위) |
| `vat_fs_est_purchase` | numeric |  | 추정 매입 |
| `inventory` | numeric |  | 재고 (Supply 시뮬 buffer) |
| `value_added_year_fin` | numeric |  | 부가가치 |
| `employees_count` | int |  | 종업원수 |
| `cri_score` | numeric(4,2) |  | CRI 1.00~10.00 |
| `cri_year` | int |  | CRI 기준연도 |
| `watch_grade` | string(10) |  | Watch 등급 |

샘플:
```csv
firm_id,biz_no,rep_bizno,firm_name,sector_code,base_year,sales_year_fin,...
F00001,1208147521,1208147521,에이전자,C26,2024,1500000000,...
```

---

## supplies.csv

Neo4j: `:SUPPLIES` 엣지 (PG 미적재 — 그래프 본질).

| 컬럼 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `source_id` | string(6) | ★ | 공급자 Firm.firm_id |
| `target_id` | string(6) | ★ | 구매자 Firm.firm_id |
| `year` | int | ★ | 멀티엣지 키 |
| `amount` | numeric | ★ | 거래금액 z_ij |
| `observed_flag` | string |  | forward/reverse |
| `obl_yn` | string(1) |  | Y/N |
| `number_observed_month` | int |  | 관측 개월수 |
| `source_cate` | string |  | 일반/세관 |
| `target_cate` | string |  | 일반/정부 / B2C / GOV (H 행렬에서 B2C/GOV 제외) |
| `purchase_weight` | float |  | a_ij = z_ij/매입_j (Leontief A; 없으면 후행 계산) |
| `sales_weight` | float |  | b_ij = z_ij/매출_i (Ghosh B; 없으면 후행 계산) |

샘플:
```csv
source_id,target_id,year,amount,observed_flag,obl_yn,number_observed_month,source_cate,target_cate,purchase_weight,sales_weight
F00001,F00002,2024,400000000,forward,Y,12,일반,일반,0.0488,0.2667
```

---

## trade.csv

Neo4j: `:EXPORTS_TO / :IMPORTS_FROM / :TRADES_PRODUCT`.

| 컬럼 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `firm_id` | string(6) | ★ | Firm.firm_id |
| `hs6` | string(6) | ★ | HS 6자리 (HSCode.code) |
| `iso_alpha2` | string(2) | ★ | 상대국 (Country.iso_alpha2) |
| `year` | int | ★ | 기준연도 |
| `direction` | string | ★ | `EXP` / `IMP` |
| `amount` | numeric |  | 추정 거래금액 |
| `weight_hs` | float |  | HS6 비중 |
| `weight_nation` | float |  | 국가 비중 |
| `rank` | int |  | 거래 순위 |

샘플:
```csv
firm_id,hs6,iso_alpha2,year,direction,amount,weight_hs,weight_nation,rank
F00002,854231,US,2024,EXP,1500000000,0.45,0.60,1
```

---

## masters/sectors.csv

PG `sectors` + Neo4j `:Sector`.

| 컬럼 | 타입 | 필수 | 의미 |
|---|---|---|---|
| `code` | string(10) | ★ | KSIC 코드 (PK) |
| `name` | text |  | 산업명 |
| `level` | int |  | KSIC 단계 (1~5) |
| `parent_code` | string(10) |  | 상위 코드 |
| `color` | string(7) |  | UI 색상 hex (`#1f77b4` 등) |

## masters/hs_codes.csv

| 컬럼 | 필수 | 의미 |
|---|---|---|
| `code` | ★ | HS6 (PK) |
| `name` |  | 품목명 |
| `hs2` |  | 상위 HS2 |
| `hs4` |  | 상위 HS4 |
| `elasticity` |  | CEPII 가격탄력성 |

## masters/countries.csv

| 컬럼 | 필수 | 의미 |
|---|---|---|
| `iso_alpha2` | ★ | ISO 3166-1 alpha-2 (PK) |
| `name_kr` |  | 한글명 |
| `name_en` |  | 영문명 |

---

## 컬럼명이 다를 때 — generic upload

원천 CSV 컬럼명이 위와 다르다면 두 가지 방법:

### A. `upload-pg` / `upload-neo4j` 의 `--rename` 사용

```bash
python -m nice_poc.etl upload-pg /data/raw_firms.csv \
    --table firms \
    --pk firm_id \
    --rename "기업ID=firm_id,기업명=firm_name,업종=sector_code,매출_재무=sales_year_fin"
```

### B. Cypher 적재 (그래프 관계까지 한 번에)

`/tmp/firms_merge.cypher`:
```cypher
UNWIND $rows AS row
MERGE (f:Firm {firm_id: row.firm_id})
SET f.firm_name = row.firm_name,
    f.sector_code = row.sector_code
MERGE (s:Sector {code: row.sector_code})
MERGE (f)-[:IN_SECTOR]->(s)
```

```bash
python -m nice_poc.etl upload-neo4j /data/raw_firms.csv \
    --cypher-file /tmp/firms_merge.cypher \
    --rename "기업ID=firm_id,기업명=firm_name,업종=sector_code"
```

### C. `--dry-run` 으로 사전 검증

행수 확인 + 컬럼 누락 검출만 하고 실제 적재는 안 함.

```bash
python -m nice_poc.etl upload-pg /data/raw_firms.csv \
    --table firms --pk firm_id --dry-run
```
