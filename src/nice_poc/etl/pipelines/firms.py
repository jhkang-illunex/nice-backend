"""firms 적재 — PG + :Firm(Neo4j) + 구조 관계 4종.

Neo4j 관계: :IN_SECTOR, :BELONGS_TO (rep_bizno 있을 때), :LOCATED_IN (KR 기본),
             :OBSERVED_IN (base_year)
"""
from __future__ import annotations

from dataclasses import dataclass

from nice_poc.etl.sinks import Neo4jSink, PgSink
from nice_poc.etl.sources import FirmsSource

FIRMS_PG_COLUMNS = (
    "firm_id", "biz_no", "rep_bizno", "firm_name", "sector_code",
    "firm_data_type", "firm_confidence_level", "base_year",
    "sales_year_fin", "sales_year_vat_observed",
    "vat_fs_est_sales", "vat_fs_est_purchase",
    "inventory", "value_added_year_fin", "employees_count",
    "cri_score", "cri_year", "watch_grade",
)

FIRM_MERGE = """
UNWIND $rows AS row
MERGE (f:Firm {firm_id: row.firm_id})
SET f.biz_no = row.biz_no,
    f.rep_bizno = row.rep_bizno,
    f.firm_name = row.firm_name,
    f.sector_code = row.sector_code,
    f.firm_data_type = row.firm_data_type,
    f.firm_confidence_level = row.firm_confidence_level,
    f.base_year = row.base_year,
    f.sales_year_fin = row.sales_year_fin,
    f.sales_year_vat_observed = row.sales_year_vat_observed,
    f.vat_fs_est_sales = row.vat_fs_est_sales,
    f.vat_fs_est_purchase = row.vat_fs_est_purchase,
    f.inventory = row.inventory,
    f.value_added_year_fin = row.value_added_year_fin,
    f.employees_count = row.employees_count,
    f.cri_score = row.cri_score,
    f.cri_year = row.cri_year,
    f.watch_grade = row.watch_grade
"""

FIRM_IN_SECTOR = """
UNWIND $rows AS row
MATCH (f:Firm {firm_id: row.firm_id})
MATCH (s:Sector {code: row.sector_code})
MERGE (f)-[:IN_SECTOR]->(s)
"""

FIRM_OBSERVED_IN = """
UNWIND $rows AS row
MATCH (f:Firm {firm_id: row.firm_id})
MERGE (y:Year {year: row.base_year})
MERGE (f)-[:OBSERVED_IN]->(y)
"""

FIRM_BELONGS_TO = """
UNWIND $rows AS row
MATCH (f:Firm {firm_id: row.firm_id})
MERGE (h:Headquarter {rep_bizno: row.rep_bizno})
MERGE (f)-[:BELONGS_TO]->(h)
"""

FIRM_LOCATED_IN_KR = """
UNWIND $rows AS row
MATCH (f:Firm {firm_id: row.firm_id})
MERGE (c:Country {iso_alpha2: 'KR'})
MERGE (f)-[:LOCATED_IN]->(c)
"""


@dataclass(frozen=True, slots=True)
class FirmsReport:
    pg_rows: int
    neo4j_nodes: int
    in_sector: int
    belongs_to: int
    observed_in: int
    located_in: int


def load_firms(
    source: FirmsSource,
    *,
    pg: PgSink | None = None,
    neo4j: Neo4jSink | None = None,
) -> FirmsReport:
    pg = pg or PgSink()
    neo4j = neo4j or Neo4jSink()

    firms = source.firms()
    rows_pg = pg.upsert("firms", firms, pk=["firm_id"], columns=FIRMS_PG_COLUMNS)
    nodes_n = neo4j.run_unwind(FIRM_MERGE, firms)

    in_sec = neo4j.run_unwind(
        FIRM_IN_SECTOR, firms.loc[firms["sector_code"].notna(), ["firm_id", "sector_code"]]
    )

    has_year = firms.loc[firms["base_year"].notna(), ["firm_id", "base_year"]].copy()
    if not has_year.empty:
        has_year["base_year"] = has_year["base_year"].astype(int)
    obs = neo4j.run_unwind(FIRM_OBSERVED_IN, has_year)

    belongs = neo4j.run_unwind(
        FIRM_BELONGS_TO,
        firms.loc[firms["rep_bizno"].notna(), ["firm_id", "rep_bizno"]],
    )

    loc = neo4j.run_unwind(FIRM_LOCATED_IN_KR, firms[["firm_id"]])

    return FirmsReport(rows_pg, nodes_n, in_sec, belongs, obs, loc)


__all__ = ["load_firms", "FirmsReport"]
