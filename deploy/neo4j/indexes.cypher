// Neo4j 설계서 §7.3 (인덱스 전략 핵심)
// - Firm 화면 필터 표준
// - 관계 프로퍼티 인덱스 (Neo4j 5.x 필수)
// - Firm.firm_name 풀텍스트 (검색바)
//
// 이외의 traversal-편의 인덱스(:EXPORTS_TO.year, :APPLIES_TO.target_type 등)는
// ETL/시뮬레이션 워크로드 도입 후 실제 쿼리 플랜을 보고 추가한다.

CREATE INDEX firm_sector_idx       IF NOT EXISTS FOR (n:Firm) ON (n.sector_code);
CREATE INDEX firm_bizno_idx        IF NOT EXISTS FOR (n:Firm) ON (n.biz_no);
CREATE INDEX firm_base_year_idx    IF NOT EXISTS FOR (n:Firm) ON (n.base_year);

CREATE FULLTEXT INDEX firm_name_fts IF NOT EXISTS FOR (n:Firm) ON EACH [n.firm_name];

CREATE INDEX supplies_year_idx     IF NOT EXISTS FOR ()-[r:SUPPLIES]-() ON (r.year);
CREATE INDEX impacts_run_idx       IF NOT EXISTS FOR ()-[r:IMPACTS]-()  ON (r.run_id);
