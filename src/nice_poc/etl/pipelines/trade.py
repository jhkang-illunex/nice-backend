""":EXPORTS_TO / :IMPORTS_FROM / :TRADES_PRODUCT 적재 — Neo4j 전용.

direction 컬럼(EXP/IMP)에 따라 적절한 관계로 적재.
"""
from __future__ import annotations

from dataclasses import dataclass

from nice_poc.etl.sinks import Neo4jSink
from nice_poc.etl.sources import TradeSource

EXPORTS_MERGE = """
UNWIND $rows AS row
MATCH (f:Firm {firm_id: row.firm_id})
MATCH (c:Country {iso_alpha2: row.iso_alpha2})
MERGE (f)-[r:EXPORTS_TO {year: row.year, hs6: row.hs6}]->(c)
SET r.amount = row.amount,
    r.weight_hs = row.weight_hs,
    r.weight_nation = row.weight_nation,
    r.rank = row.rank
"""

IMPORTS_MERGE = """
UNWIND $rows AS row
MATCH (f:Firm {firm_id: row.firm_id})
MATCH (c:Country {iso_alpha2: row.iso_alpha2})
MERGE (f)-[r:IMPORTS_FROM {year: row.year, hs6: row.hs6}]->(c)
SET r.amount = row.amount,
    r.weight_hs = row.weight_hs,
    r.weight_nation = row.weight_nation,
    r.rank = row.rank
"""

TRADES_PRODUCT_MERGE = """
UNWIND $rows AS row
MATCH (f:Firm {firm_id: row.firm_id})
MERGE (h:HSCode {code: row.hs6})
MERGE (f)-[r:TRADES_PRODUCT {year: row.year, direction: row.direction}]->(h)
SET r.amount = row.amount,
    r.weight_hs = row.weight_hs
"""


@dataclass(frozen=True, slots=True)
class TradeReport:
    exports_to: int
    imports_from: int
    trades_product: int


def load_trade(
    source: TradeSource,
    *,
    neo4j: Neo4jSink | None = None,
) -> TradeReport:
    neo4j = neo4j or Neo4jSink()
    df = source.trade()

    exp = df[df["direction"] == "EXP"]
    imp = df[df["direction"] == "IMP"]

    exp_n = neo4j.run_unwind(EXPORTS_MERGE, exp)
    imp_n = neo4j.run_unwind(IMPORTS_MERGE, imp)
    prod_n = neo4j.run_unwind(TRADES_PRODUCT_MERGE, df)

    return TradeReport(exports_to=exp_n, imports_from=imp_n, trades_product=prod_n)
