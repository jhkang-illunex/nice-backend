"""원천 → PostgreSQL / Neo4j 데이터 적재.

세 축 분리:
- ``etl.sources``  : 원천 어댑터 (CSV 기본, 추후 PostgresRawSource)
- ``etl.sinks``    : 목적지 어댑터 (PgSink, Neo4jSink)
- ``etl.pipelines``: 도메인별 (masters / firms / supplies / trade)

CLI 진입점은 ``python -m nice_poc.etl --help`` 참조.
"""
