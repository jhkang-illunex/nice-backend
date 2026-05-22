from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from neo4j import Driver, GraphDatabase, Session

from nice_poc.config import get_settings


@lru_cache
def get_neo4j_driver() -> Driver:
    s = get_settings()
    return GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))


@contextmanager
def neo4j_session(database: str | None = None) -> Iterator[Session]:
    s = get_settings()
    with get_neo4j_driver().session(database=database or s.neo4j_database) as session:
        yield session
