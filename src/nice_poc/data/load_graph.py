"""Neo4j → pandas DataFrame 추출. 구현명세서 §1.3, §8.1.

::

    data = load_graph.from_neo4j(year=2024)
    firms, edges, exports = data["firms"], data["edges"], data["exports"]
"""
from __future__ import annotations

import pandas as pd

from nice_poc.db import neo4j_session

FIRMS_QUERY = """
MATCH (f:Firm)
OPTIONAL MATCH (f)-[:IN_SECTOR]->(s:Sector)
RETURN f.firm_id              AS firm_id,
       f.biz_no               AS biz_no,
       f.rep_bizno            AS rep_bizno,
       f.firm_name            AS firm_name,
       coalesce(f.sector_code, s.code) AS sector_code,
       f.base_year            AS base_year,
       f.sales_year_fin       AS sales_year_fin,
       f.sales_year_vat_observed AS sales_year_vat_observed,
       f.vat_fs_est_sales     AS vat_fs_est_sales,
       f.vat_fs_est_purchase  AS vat_fs_est_purchase,
       f.inventory            AS inventory,
       f.cri_score            AS cri_score,
       f.cri_year             AS cri_year
"""

SUPPLIES_QUERY = """
MATCH (a:Firm)-[r:SUPPLIES {year: $year}]->(b:Firm)
RETURN a.firm_id        AS source_id,
       b.firm_id        AS target_id,
       r.year           AS year,
       r.amount         AS amount,
       r.source_cate    AS source_cate,
       r.target_cate    AS target_cate,
       r.purchase_weight AS purchase_weight,
       r.sales_weight   AS sales_weight
"""

EXPORTS_QUERY = """
MATCH (f:Firm)-[r:EXPORTS_TO {year: $year}]->(c:Country)
RETURN f.firm_id     AS firm_id,
       r.hs6         AS hs6,
       c.iso_alpha2  AS iso_alpha2,
       r.year        AS year,
       r.amount      AS amount,
       r.weight_hs   AS weight_hs,
       r.weight_nation AS weight_nation
"""

IMPORTS_QUERY = """
MATCH (f:Firm)-[r:IMPORTS_FROM {year: $year}]->(c:Country)
RETURN f.firm_id     AS firm_id,
       r.hs6         AS hs6,
       c.iso_alpha2  AS iso_alpha2,
       r.year        AS year,
       r.amount      AS amount,
       r.weight_hs   AS weight_hs,
       r.weight_nation AS weight_nation
"""


def from_neo4j(year: int, *, database: str | None = None) -> dict[str, pd.DataFrame]:
    with neo4j_session(database=database) as s:
        firms_rows = s.run(FIRMS_QUERY).data()
        edges_rows = s.run(SUPPLIES_QUERY, year=year).data()
        exports_rows = s.run(EXPORTS_QUERY, year=year).data()
        imports_rows = s.run(IMPORTS_QUERY, year=year).data()

    firms = pd.DataFrame(firms_rows).set_index("firm_id")
    edges = pd.DataFrame(edges_rows)
    exports = pd.DataFrame(exports_rows)
    imports = pd.DataFrame(imports_rows)

    return {"firms": firms, "edges": edges, "exports": exports, "imports": imports}


__all__ = ["from_neo4j"]
