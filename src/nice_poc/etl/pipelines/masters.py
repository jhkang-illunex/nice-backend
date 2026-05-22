"""sectors / hs_codes / countries 적재.

PG (FK 참조용) + Neo4j (그래프 매개 노드) 양쪽에 적재.
"""

from __future__ import annotations

from dataclasses import dataclass

from nice_poc.etl.sinks import Neo4jSink, PgSink
from nice_poc.etl.sources import MastersSource

SECTOR_MERGE = """
UNWIND $rows AS row
MERGE (s:Sector {code: row.code})
SET s.name = row.name,
    s.level = row.level,
    s.parent_code = row.parent_code,
    s.color = row.color
"""

HSCODE_MERGE = """
UNWIND $rows AS row
MERGE (h:HSCode {code: row.code})
SET h.name = row.name,
    h.hs2 = row.hs2,
    h.hs4 = row.hs4,
    h.elasticity = row.elasticity
"""

COUNTRY_MERGE = """
UNWIND $rows AS row
MERGE (c:Country {iso_alpha2: row.iso_alpha2})
SET c.name_kr = row.name_kr,
    c.name_en = row.name_en
"""


@dataclass(frozen=True, slots=True)
class MastersReport:
    sectors_pg: int
    sectors_neo4j: int
    hs_codes_pg: int
    hs_codes_neo4j: int
    countries_pg: int
    countries_neo4j: int


def load_masters(
    source: MastersSource,
    *,
    pg: PgSink | None = None,
    neo4j: Neo4jSink | None = None,
) -> MastersReport:
    pg = pg or PgSink()
    neo4j = neo4j or Neo4jSink()

    sectors = source.sectors()
    hs_codes = source.hs_codes()
    countries = source.countries()

    sec_pg = pg.upsert("sectors", sectors, pk=["code"])
    hs_pg = pg.upsert("hs_codes", hs_codes, pk=["code"])
    co_pg = pg.upsert("countries", countries, pk=["iso_alpha2"])

    sec_n = neo4j.run_unwind(SECTOR_MERGE, sectors)
    hs_n = neo4j.run_unwind(HSCODE_MERGE, hs_codes)
    co_n = neo4j.run_unwind(COUNTRY_MERGE, countries)

    return MastersReport(sec_pg, sec_n, hs_pg, hs_n, co_pg, co_n)
