"""baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-22 17:18:17.940167

PoC 1차 부트스트랩 시점의 PG schema (8 테이블 + MV 2 + 확장 3) 는
이미 `deploy/postgres/init/*.sql` 이 docker entrypoint 로 적용했다.
본 revision 은 그 형상을 Alembic 의 시작점으로 박기 위한 빈 baseline
이다 — upgrade/downgrade 모두 no-op 이며, 라이브 DB 에는 `alembic stamp head`
로만 메타데이터를 기록한다. 이후 schema 변경은 신규 revision 으로 추적.

ADR-004 참조.
"""
from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: schema 는 deploy/postgres/init/*.sql 로 이미 적용됨."""
    pass


def downgrade() -> None:
    """No-op: baseline 의 downgrade 는 정의하지 않는다."""
    pass
