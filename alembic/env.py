"""Alembic environment.

DSN 은 `alembic.ini` 에 박지 않고 `nice_poc.config.get_settings()` 에서
런타임 주입한다. ADR-004 참조.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# alembic 은 프로젝트 루트에서 호출되므로 src 레이아웃을 sys.path 에 보장.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SRC = _PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from nice_poc.config import get_settings  # noqa: E402

# Alembic Config 객체.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DSN 주입 — alembic.ini 의 sqlalchemy.url 은 비어 있다.
config.set_main_option("sqlalchemy.url", get_settings().postgres_dsn)

# autogenerate 미사용 (현재 baseline 만 박는 단계). 향후 SQLAlchemy 모델을
# 도입하면 여기서 Base.metadata 를 바인딩한다.
target_metadata = None


# 운영 PG 의 public 스키마(NICE 운영 31 테이블)와 격리 — alembic_version 까지
# 모두 rag schema 에 둔다. 운영자 시야에서 우리 PoC 흔적이 한 schema 로 묶임.
_VERSION_TABLE_SCHEMA = "rag"


def run_migrations_offline() -> None:
    """Offline 모드 — Engine 없이 URL 만으로 SQL 스크립트를 생성."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=_VERSION_TABLE_SCHEMA,
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online 모드 — 실제 DB 연결로 마이그레이션 실행."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=_VERSION_TABLE_SCHEMA,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
