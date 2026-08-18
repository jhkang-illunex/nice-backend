"""ksic — 한국표준산업분류(제11차) 대분류·중분류 테이블 + 검색 색인 + 임베딩.

스키마 결정 근거
  - code: TEXT PK. 대분류는 영문 1자(A~U), 중분류는 2자리 숫자('01'~'99') —
          자릿수·문자종이 섞여 있어 텍스트가 자연스럽다. 선행 0 보존.
  - level: 1=대분류, 2=중분류. 요구 범위가 "중분류(2자리)까지" 이므로
           소분류(3) 이하는 적재하지 않는다 — 대신 하위 항목명은
           children_text 로 결합되어 검색 리콜을 담당한다.
  - children_text: 하위(소·세·세세분류) 항목명 결합. 중분류 명칭만으로는
           '반도체' 같은 구체 업종어가 색인에 없어 키워드/임베딩 매칭이
           죽는다 — 예: 중분류 26 '전자 부품, 컴퓨터, … 제조업' 은
           소분류 261 '반도체 제조업' 이 있어야 '반도체' 질의에 걸린다.
  - search_text: 임베딩 입력 + ts 색인의 단일 진실 (ingest 파이프라인 생성).
  - search_tsv: hsk 와 달리 GENERATED STORED 유지 가능 — search_text 가
           동일 테이블 컬럼이라 IMMUTABLE 제약을 깨지 않는다.
           setweight A=항목명, B=하위 항목명으로 명칭 직격 매칭을 우대.
  - embedding: vector(1024) — rag.hsk 와 동일 백엔드(BAAI/bge-m3) 공유.
           적재는 별 단계(``nice_ingest run ksic_embed``).

인덱스는 98 row 규모라 성능상 무의미하지만 rag.hsk 와 동일 전략으로 통일
(운영 점검·EXPLAIN 시 대칭성 유지).

Revision ID: 0006_ksic
Revises: 0005_hs_heading
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_ksic"
down_revision: str | Sequence[str] | None = "0005_hs_heading"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS rag;")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag.ksic (
            code            TEXT PRIMARY KEY,

            level           SMALLINT NOT NULL CHECK (level IN (1, 2)),
            parent_code     TEXT REFERENCES rag.ksic (code),

            name_ko         TEXT NOT NULL,
            -- 대분류만: 해당 대분류가 포괄하는 중분류 코드 범위 (예: '10~34')
            division_range  TEXT,

            -- 하위(소·세·세세분류) 항목명 결합 — ingest 가 생성
            children_text   TEXT,
            -- 검색/임베딩의 단일 진실: name_ko | 상위명 | children_text
            search_text     TEXT,

            search_tsv tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('simple'::regconfig, coalesce(name_ko, '')), 'A')
                || setweight(to_tsvector('simple'::regconfig, coalesce(children_text, '')), 'B')
            ) STORED,

            embedding vector(1024),

            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS ksic_level_idx ON rag.ksic (level);")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ksic_search_tsv_idx "
        "ON rag.ksic USING GIN (search_tsv);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ksic_name_ko_trgm_idx "
        "ON rag.ksic USING GIN (name_ko gin_trgm_ops);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ksic_embedding_hnsw_idx "
        "ON rag.ksic USING hnsw (embedding vector_cosine_ops);"
    )

    # updated_at 자동 갱신 — rag.hsk 와 동일 패턴 (테이블별 함수로 격리 유지)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION rag.ksic_touch_updated_at()
        RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS ksic_touch_updated_at_trg ON rag.ksic;
        CREATE TRIGGER ksic_touch_updated_at_trg
            BEFORE UPDATE ON rag.ksic
            FOR EACH ROW EXECUTE FUNCTION rag.ksic_touch_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ksic_touch_updated_at_trg ON rag.ksic;")
    op.execute("DROP FUNCTION IF EXISTS rag.ksic_touch_updated_at();")
    op.execute("DROP TABLE IF EXISTS rag.ksic CASCADE;")
