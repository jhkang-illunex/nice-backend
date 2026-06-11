"""검색 자기보완 루프 — search_log(저신뢰 질의 큐) + synonyms(동의어 사전).

폐쇄망 운영 설계: 질의 다양화로 검색 품질이 떨어질 때 외부 갱신 없이
스스로 보완하는 루프의 저장소.

  search_log : 모든 /search·/agent 질의와 RRF top1 점수를 기록.
               top_score < 임계치 → low_confidence (미해결 큐).
  synonyms   : 통칭 → 색인 용어 확장 사전. 코드 내 빌트인(_BUILTIN)과
               런타임 병합되며, hsk_synonym_learn 배치가 self-play 검증을
               통과한 항목을 source='auto' 로 자동 등록한다.

Revision ID: 0004_search_log_synonyms
Revises: 0003_hsk_search_v3
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_search_log_synonyms"
down_revision: str | Sequence[str] | None = "0003_hsk_search_v3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag.search_log (
            id              BIGSERIAL PRIMARY KEY,
            query           TEXT NOT NULL,
            query_expanded  TEXT,
            top_score       NUMERIC(8,6),
            top_codes       TEXT[],
            low_confidence  BOOLEAN NOT NULL DEFAULT false,
            resolved        BOOLEAN NOT NULL DEFAULT false,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    # 미해결 저신뢰 큐 조회 전용 부분 인덱스
    op.execute(
        "CREATE INDEX IF NOT EXISTS search_log_lowconf_idx "
        "ON rag.search_log (created_at DESC) "
        "WHERE low_confidence AND NOT resolved;"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rag.synonyms (
            alias           TEXT PRIMARY KEY,
            expansion       TEXT NOT NULL,
            source          TEXT NOT NULL DEFAULT 'auto',
            verified_score  NUMERIC(8,6),
            enabled         BOOLEAN NOT NULL DEFAULT true,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag.search_log;")
    op.execute("DROP TABLE IF EXISTS rag.synonyms;")
