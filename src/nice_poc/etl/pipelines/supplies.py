""":SUPPLIES 엣지 적재 — Neo4j 전용 (스펙 §8.1: 거래는 그래프만 보유).

연도별 멀티엣지(MERGE 키 = year). purchase_weight / sales_weight 는 ETL 단계에서
계산된 값이 있다면 그대로 적재, 없으면 amount 만 적재하고 후처리에 위임.
"""

from __future__ import annotations

from dataclasses import dataclass

from nice_poc.etl.sinks import Neo4jSink
from nice_poc.etl.sources import SuppliesSource

SUPPLIES_MERGE = """
UNWIND $rows AS row
MATCH (s:Firm {firm_id: row.source_id})
MATCH (t:Firm {firm_id: row.target_id})
MERGE (s)-[r:SUPPLIES {year: row.year}]->(t)
SET r.amount = row.amount,
    r.observed_flag = row.observed_flag,
    r.obl_yn = row.obl_yn,
    r.number_observed_month = row.number_observed_month,
    r.source_cate = row.source_cate,
    r.target_cate = row.target_cate,
    r.purchase_weight = row.purchase_weight,
    r.sales_weight = row.sales_weight
"""


@dataclass(frozen=True, slots=True)
class SuppliesReport:
    edges: int


def load_supplies(
    source: SuppliesSource,
    *,
    neo4j: Neo4jSink | None = None,
) -> SuppliesReport:
    neo4j = neo4j or Neo4jSink()
    df = source.supplies()
    return SuppliesReport(edges=neo4j.run_unwind(SUPPLIES_MERGE, df))
