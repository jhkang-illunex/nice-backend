-- 아키텍처 설계서 §2.1
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
-- mecab_ko 는 별도 빌드 필요. 운영 1.1 단계에서 도입 (ADR-002 참조).
