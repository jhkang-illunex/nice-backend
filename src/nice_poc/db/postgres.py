from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from nice_poc.config import get_settings


@lru_cache
def get_pg_engine() -> Engine:
    s = get_settings()
    return create_engine(
        s.postgres_dsn,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        future=True,
    )


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_pg_engine(), expire_on_commit=False, future=True)


@contextmanager
def pg_session() -> Iterator[Session]:
    with _session_factory()() as session:
        yield session
