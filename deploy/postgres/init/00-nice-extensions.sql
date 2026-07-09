-- NICE PostgreSQL 기본 확장 자동 생성.
--   실행 컨텍스트: docker-entrypoint 가 이 파일을 POSTGRES_DB 에 대해 psql 로 실행.
--   template1 에도 만들어, 이후 생성되는 모든 DB 가 확장을 상속하게 한다.
--
-- 대상 확장 (NICE 운영 DB 기준):
--   vector     — pgvector, 임베딩 hnsw 인덱스 (rag.hsk.embedding). base 이미지 제공.
--   pg_trgm    — 트라이그램 GIN 인덱스 (rag.hsk 검색 트라이그램). contrib.
--   btree_gin  — 복합 GIN 인덱스용(예비). contrib.

-- (1) 기본 DB (POSTGRES_DB)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- (2) template1 → 이후 CREATE DATABASE 로 만드는 DB 가 자동 상속
\connect template1
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
