"""hsk 검색 v3 — search_text/search_tsv 일반 컬럼화 + detail_ko/en 추가.

배경
  0002 의 search_text/search_tsv 는 GENERATED STORED 였다. v3 에서는 KIS HS
  계층(s_ra417)의 부모 chain(detail_ko/en)을 search_text 에 포함시키는데,
  GENERATED 표현식은 타 테이블 참조가 불가하므로 일반 컬럼으로 전환하고
  데이터 갱신은 ``nice_ingest run hsk_enrich`` 파이프라인이 담당한다.

  운영 DB 에는 2026-06-09~11 사이 ad-hoc SQL 로 이미 적용된 상태 — 본
  리비전은 그 드리프트의 성문화이며, 모든 단계는 멱등(이미 적용된 DB 에서
  no-op)이다. search_text_v1 백업 컬럼은 v1 포맷이 0002 표현식으로 언제든
  재구성 가능하므로 제거한다.

Revision ID: 0003_hsk_search_v3
Revises: 0002_hsk
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_hsk_search_v3"
down_revision: str | Sequence[str] | None = "0002_hsk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # GENERATED → 일반 컬럼 (DROP EXPRESSION 은 기존 STORED 값을 보존한다).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_attribute
                WHERE attrelid = 'rag.hsk'::regclass
                  AND attname = 'search_text' AND attgenerated = 's'
            ) THEN
                ALTER TABLE rag.hsk ALTER COLUMN search_text DROP EXPRESSION;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_attribute
                WHERE attrelid = 'rag.hsk'::regclass
                  AND attname = 'search_tsv' AND attgenerated = 's'
            ) THEN
                ALTER TABLE rag.hsk ALTER COLUMN search_tsv DROP EXPRESSION;
            END IF;
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE rag.hsk "
        "ADD COLUMN IF NOT EXISTS detail_ko TEXT, "
        "ADD COLUMN IF NOT EXISTS detail_en TEXT;"
    )
    op.execute("ALTER TABLE rag.hsk DROP COLUMN IF EXISTS search_text_v1;")


def downgrade() -> None:
    # DROP EXPRESSION 은 비가역 — v1 포맷 복원은 컬럼 재생성으로만 가능.
    op.execute("ALTER TABLE rag.hsk DROP COLUMN IF EXISTS detail_ko;")
    op.execute("ALTER TABLE rag.hsk DROP COLUMN IF EXISTS detail_en;")
    op.execute("ALTER TABLE rag.hsk DROP COLUMN IF EXISTS search_text, DROP COLUMN IF EXISTS search_tsv;")
    op.execute(
        """
        ALTER TABLE rag.hsk
        ADD COLUMN search_text TEXT GENERATED ALWAYS AS (
            coalesce(name_ko, '')               || ' | ' ||
            coalesce(standard_trade_name, '')   || ' | ' ||
            coalesce(nature_integrated_name, '')|| ' | ' ||
            coalesce(name_en, '')               || ' | ' ||
            coalesce(hs_content, '')
        ) STORED,
        ADD COLUMN search_tsv tsvector GENERATED ALWAYS AS (
            to_tsvector('simple'::regconfig,
                coalesce(name_ko, '') || ' ' ||
                coalesce(standard_trade_name, '') || ' ' ||
                coalesce(nature_integrated_name, '') || ' ' ||
                coalesce(name_en, '')
            )
        ) STORED;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS hsk_search_tsv_idx "
        "ON rag.hsk USING GIN (search_tsv);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS hsk_search_text_trgm_idx "
        "ON rag.hsk USING GIN (search_text gin_trgm_ops);"
    )
