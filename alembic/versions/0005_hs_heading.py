"""관세청 공식 단위별 품목명 — rag.hs_heading + hsk.heading_ko.

KIS 계층(s_ra417)은 일부 품목에서 중간 계층(특히 5단위) 명칭을 생략한다
(예: 8505.1 '영구자석…', 7219.3 '냉간압연…', 1001.1 '듀럼종 밀'). 그 결과
사용자가 쓰는 공식 용어가 색인에 부재해 trg/ts 시그널이 발동하지 못한다
— 측정 결과 12,469개 중 85.8% 코드에서 공식 계층 명칭 17,578건 누락.

  hs_heading : 관세청 'HS부호 단위별 품목명' (공공데이터포털 15130660),
               2~10단위 prefix → 공식 한글/영문 명칭.
  heading_ko : 코드별 4~9단위 공식 명칭 chain (hsk_enrich 가 생성·병합).

Revision ID: 0005_hs_heading
Revises: 0004_search_log_synonyms
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_hs_heading"
down_revision: str | Sequence[str] | None = "0004_search_log_synonyms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag.hs_heading (
            hs_prefix   TEXT PRIMARY KEY,
            level       SMALLINT NOT NULL,
            name_ko     TEXT,
            name_en     TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE rag.hsk ADD COLUMN IF NOT EXISTS heading_ko TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE rag.hsk DROP COLUMN IF EXISTS heading_ko")
    op.execute("DROP TABLE IF EXISTS rag.hs_heading")
