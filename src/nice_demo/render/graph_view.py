"""Subgraph + LLM 분류 결과 → streamlit-agraph Node/Edge.

색상 규약
  hop=0 (시드)                : #E74C3C (빨강)
  category=HIGH  (LLM 1차)    : #F39C12 (주황)
  category=MEDIUM (LLM 1차)   : #F7DC6F (노랑)
  category=LOW/NONE 또는 미분류 : #BDC3C7 (회색)
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from streamlit_agraph import Config, Edge, Node, agraph

from nice_demo.pipeline.subgraph import Subgraph

_COLOR_SEED = "#E74C3C"
_COLOR_HIGH = "#F39C12"
_COLOR_MEDIUM = "#F7DC6F"
_COLOR_OTHER = "#BDC3C7"

_PRIMARY_CATEGORIES = frozenset({"HIGH", "MEDIUM"})


@dataclass(frozen=True)
class GraphRenderConfig:
    height: int = 700
    width: int = 1100
    physics: bool = True
    hierarchical: bool = False


def _node_color(hop: int, category: str | None) -> str:
    if hop == 0:
        return _COLOR_SEED
    if category == "HIGH":
        return _COLOR_HIGH
    if category == "MEDIUM":
        return _COLOR_MEDIUM
    return _COLOR_OTHER


def _node_label(bizno: str, name_ko: str | None) -> str:
    if name_ko:
        return f"{name_ko}\n({bizno})"
    return bizno


def to_agraph(
    sg: Subgraph,
    *,
    categories: dict[str, str] | None = None,
    cfg: GraphRenderConfig | None = None,
) -> tuple[list[Node], list[Edge], Config]:
    """Subgraph → agraph 입력 3종 (nodes/edges/config)."""
    cfg = cfg or GraphRenderConfig()
    categories = categories or {}

    nodes: list[Node] = []
    for row in sg.nodes.itertuples(index=False):
        cat = categories.get(row.bizno)
        nodes.append(
            Node(
                id=row.bizno,
                label=_node_label(row.bizno, getattr(row, "name_ko", None)),
                size=22 if row.hop == 0 else 14,
                color=_node_color(int(row.hop), cat),
                title=_tooltip(row, cat),
            )
        )

    edges: list[Edge] = []
    for e in sg.edges.itertuples(index=False):
        edges.append(
            Edge(
                source=e.source_id,
                target=e.target_id,
                label=f"{int(e.amount):,}" if pd.notna(e.amount) else "",
                color="#95a5a6",
            )
        )

    config = Config(
        height=cfg.height,
        width=cfg.width,
        directed=True,
        physics=cfg.physics,
        hierarchical=cfg.hierarchical,
        nodeHighlightBehavior=True,
        node={"labelProperty": "label", "renderLabel": True},
        link={"labelProperty": "label", "renderLabel": False},
    )
    return nodes, edges, config


def _tooltip(row, category: str | None) -> str:
    name = getattr(row, "name_ko", None) or getattr(row, "name_en", None) or "(no name)"
    rep = getattr(row, "rep_ko", None) or "-"
    cat = category or "-"
    return f"{name} | rep: {rep} | hop: {row.hop} | LLM: {cat}"


def draw(sg: Subgraph, *, categories: dict[str, str] | None = None) -> str | None:
    """Streamlit 컨텍스트 안에서 그래프를 그리고 클릭된 노드 id 를 반환."""
    nodes, edges, config = to_agraph(sg, categories=categories)
    return agraph(nodes=nodes, edges=edges, config=config)
