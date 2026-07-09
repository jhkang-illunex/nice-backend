# NICE PostgreSQL 16 베이스 이미지
#   base: pgvector/pgvector:pg16  (vector 0.8.2 — NICE 운영 DB 와 동일 버전)
#   NICE 운영에서 쓰는 확장(vector/pg_trgm/btree_gin)을 초기화 시 자동 생성한다.
#   → 이 이미지로 띄운 DB 는 CREATE EXTENSION 없이 바로 rag/shock 스키마 복원·사용 가능.
#
# 빌드:   docker build -t nice/postgres:pg16 deploy/postgres/
# 사용:   docker run -d -e POSTGRES_PASSWORD=... -e POSTGRES_DB=nice_innovation \
#                 -v pgdata:/var/lib/postgresql/data -p 5432:5432 nice/postgres:pg16
#
# 확장(.so)은 base 이미지에 이미 설치돼 있고(48개 contrib + vector),
# 아래 init 스크립트가 template1 + 기본 DB 에 CREATE EXTENSION 하여 "기본 설치" 상태로 만든다.
FROM pgvector/pgvector:pg16

# 초기화 스크립트 — 데이터 디렉터리가 비어있는 최초 기동 때 1회 실행된다.
COPY init/00-nice-extensions.sql /docker-entrypoint-initdb.d/00-nice-extensions.sql
