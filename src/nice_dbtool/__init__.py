"""nice_dbtool — DB 핸들링 모듈 (company_edge 조회·1차 시드 선정·그래프 조립·DB 기반 시나리오).

nice_graph.shock 에서 이전한 DB 의존 계층. Streamlit·라이브러리가 이걸로 그래프를
조립(DB→edges/triple)하고, 전파 계산은 순수 nice_shock(또는 run_scenario 내부)으로 한다.

  select_primary_firms(hscode, ...)      HS → ra603 거래구성 → 1차 기업
  assemble_propagation_input(seeds, ...) 1차 시드 → N-depth company_edge 확장 → 전파 입력
  run_scenario(scenario, seeds, ...)     tariff/volume (DB 조립 + 전파)
  fetch_subgraph / extract_first_target  보조

전파 엔진(ShockResult·propagate_shock)은 nice_shock.engine 재노출(하위호환).
"""
from nice_dbtool.assemble import (
    AssembledNode,
    Direction,
    Normalize,
    PropagationInput,
    assemble_propagation_input,
    make_node_id,
    parse_node_id,
    run_propagation,
)
from nice_dbtool.fetch import EdgeRow, NodeRow, SubgraphResult, fetch_subgraph
from nice_dbtool.scenario import (
    DirectionResult,
    ScenarioResult,
    VolumeSpec,
    run_scenario,
    run_tariff_shock,
    run_volume_shock,
)
from nice_dbtool.screen import ExposedFirm, PrimarySelectionResult, select_primary_firms
from nice_dbtool.target import extract_first_target
from nice_shock.engine import ShockResult, propagate_shock

__all__ = [
    "AssembledNode",
    "Direction",
    "DirectionResult",
    "EdgeRow",
    "ExposedFirm",
    "NodeRow",
    "Normalize",
    "PrimarySelectionResult",
    "PropagationInput",
    "ScenarioResult",
    "ShockResult",
    "SubgraphResult",
    "VolumeSpec",
    "assemble_propagation_input",
    "extract_first_target",
    "fetch_subgraph",
    "make_node_id",
    "parse_node_id",
    "propagate_shock",
    "run_propagation",
    "run_scenario",
    "run_tariff_shock",
    "run_volume_shock",
    "select_primary_firms",
]
