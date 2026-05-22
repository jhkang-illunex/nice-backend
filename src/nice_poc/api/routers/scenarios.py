"""화면 ② 시나리오 입력. PG insert + Neo4j MERGE 는 데이터/스토리지 확보 후."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from nice_poc.api.schemas import ScenarioCreate, ScenarioOut

router = APIRouter(prefix="/api", tags=["scenarios"])


@router.post("/scenario", response_model=ScenarioOut, status_code=201)
def create_scenario(payload: ScenarioCreate) -> ScenarioOut:
    raise HTTPException(status_code=501, detail="data not loaded yet")
