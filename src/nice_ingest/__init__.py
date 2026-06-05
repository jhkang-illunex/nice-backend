"""ingestion 잡 컨테이너 — Excel/CSV → PostgreSQL + Neo4j dual-write.

파이프라인 플러그인 패턴: 새 도메인 추가 = ``pipelines/<name>/`` 디렉터리
하나 + ``register()`` 호출. CLI 는 자동 발견.
"""

__version__ = "0.1.0"
