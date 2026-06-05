"""hsk — 관세청 HS부호 테이블 + 검색 색인 + 임베딩 컬럼.

스키마 결정 근거
  - hs_code: CHAR(10) PK. 관세청 BCD 가 10자리 고정. 앞자리 0 보존을 위해 텍스트.
  - hs2/hs4/hs6: STORED GENERATED — 시연/집계가 HS 계층 단위로 자주 들어옴.
                  GROUP BY 효율을 위해 STORED 가 합리적(데이터 12,470 row 로 비용 미미).
  - search_text: 임베딩 입력 + trigram 색인의 단일 진실. 5개 텍스트 필드 결합.
  - search_tsv:  tsvector(simple). 한국어 형태소 분석기는 운영 1.1(mecab_ko) 단계에서
                  도입. 'simple' 은 token 분리만 — pg_trgm 과 조합되면 short-query 도 견고.
  - embedding:   vector(1024). Qwen3-Embedding-0.6B 출력 차원과 일치.
                  적재는 별 단계(nice_rag.search.hsk_embed 일괄 적재 스크립트).

인덱스 전략
  - HNSW(코사인): 작은 데이터셋에 ivfflat 보다 빠르고 lists 튜닝 불요.
  - GIN(trigram): autocomplete + 짧은 한글 부분일치.
  - GIN(tsvector): 형태소 색인 진입 시 즉시 전환 가능한 자리.

Revision ID: 0002_hsk
Revises: 0001_baseline
Create Date: 2026-06-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_hsk"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hsk (
            hs_code                 CHAR(10) PRIMARY KEY,

            valid_from              DATE NOT NULL,
            valid_to                DATE NOT NULL,

            name_ko                 TEXT,
            name_en                 TEXT,
            hs_content              TEXT,
            standard_trade_name     TEXT,

            qty_unit_max_price      NUMERIC(18,4),
            weight_unit_max_price   NUMERIC(18,4),
            qty_unit_code           VARCHAR(8),
            weight_unit_code        VARCHAR(8),

            export_nature_code      VARCHAR(16),
            import_nature_code      VARCHAR(16),

            item_spec_name          TEXT,
            required_spec_name      TEXT,
            ref_spec_name           TEXT,
            spec_description        TEXT,
            spec_content            TEXT,

            nature_integrated_code  VARCHAR(16),
            nature_integrated_name  TEXT,

            -- 파생: HS 계층 (STORED GENERATED)
            hs2 CHAR(2) GENERATED ALWAYS AS (substr(hs_code, 1, 2)) STORED,
            hs4 CHAR(4) GENERATED ALWAYS AS (substr(hs_code, 1, 4)) STORED,
            hs6 CHAR(6) GENERATED ALWAYS AS (substr(hs_code, 1, 6)) STORED,

            -- 검색/임베딩의 단일 진실 텍스트
            search_text TEXT GENERATED ALWAYS AS (
                concat_ws(' | ',
                    NULLIF(name_ko, ''),
                    NULLIF(standard_trade_name, ''),
                    NULLIF(nature_integrated_name, ''),
                    NULLIF(name_en, ''),
                    NULLIF(hs_content, '')
                )
            ) STORED,

            -- 전문 검색 토큰 (simple — mecab_ko 도입 시 교체)
            search_tsv tsvector GENERATED ALWAYS AS (
                to_tsvector('simple',
                    coalesce(name_ko, '') || ' ' ||
                    coalesce(standard_trade_name, '') || ' ' ||
                    coalesce(nature_integrated_name, '') || ' ' ||
                    coalesce(name_en, '')
                )
            ) STORED,

            -- Qwen3-Embedding-0.6B 임베딩 (별 단계에서 적재)
            embedding vector(1024),

            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )

    op.execute("CREATE INDEX IF NOT EXISTS hsk_valid_to_idx ON hsk (valid_to);")
    op.execute("CREATE INDEX IF NOT EXISTS hsk_hs2_idx ON hsk (hs2);")
    op.execute("CREATE INDEX IF NOT EXISTS hsk_hs4_idx ON hsk (hs4);")
    op.execute("CREATE INDEX IF NOT EXISTS hsk_hs6_idx ON hsk (hs6);")

    op.execute(
        "CREATE INDEX IF NOT EXISTS hsk_search_tsv_idx "
        "ON hsk USING GIN (search_tsv);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS hsk_name_ko_trgm_idx "
        "ON hsk USING GIN (name_ko gin_trgm_ops);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS hsk_search_text_trgm_idx "
        "ON hsk USING GIN (search_text gin_trgm_ops);"
    )

    # HNSW (cosine). m / ef_construction 은 기본값으로 시작 — 12k 규모면 충분.
    op.execute(
        "CREATE INDEX IF NOT EXISTS hsk_embedding_hnsw_idx "
        "ON hsk USING hnsw (embedding vector_cosine_ops);"
    )

    # updated_at 자동 갱신 트리거 (upsert 시 갱신 추적)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION hsk_touch_updated_at()
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
        DROP TRIGGER IF EXISTS hsk_touch_updated_at_trg ON hsk;
        CREATE TRIGGER hsk_touch_updated_at_trg
            BEFORE UPDATE ON hsk
            FOR EACH ROW EXECUTE FUNCTION hsk_touch_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS hsk_touch_updated_at_trg ON hsk;")
    op.execute("DROP FUNCTION IF EXISTS hsk_touch_updated_at();")
    op.execute("DROP TABLE IF EXISTS hsk CASCADE;")
