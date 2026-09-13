"""POST /analyze-meal — 사진·텍스트에서 음식 후보를 뽑는다."""

from __future__ import annotations

from fastapi import APIRouter

from agents import runner
from agents.schemas import AnalyzeMealRequest, AnalyzeMealResponse
from app.config import Scenario
from app.routers._deps import ScenarioDep, apply_scenario
from guardrail.medical import find_violation

router = APIRouter(tags=["analyze"])


@router.post("/analyze-meal", response_model=AnalyzeMealResponse)
async def analyze_meal(req: AnalyzeMealRequest, scenario: ScenarioDep) -> AnalyzeMealResponse:
    await apply_scenario(scenario)

    blocked = scenario is Scenario.BLOCKED or find_violation(req.raw_text) is not None
    return runner.analyze_meal(req, scenario, blocked=blocked)
