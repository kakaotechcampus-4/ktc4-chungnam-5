"""POST /long-feedback — 주간·월간·전체 기간 추이 피드백."""

from __future__ import annotations

from fastapi import APIRouter

from agents import runner
from agents.schemas import LongFeedbackRequest, LongFeedbackResponse
from app.config import Scenario
from app.routers._deps import ScenarioDep, apply_scenario
from guardrail.medical import find_violation

router = APIRouter(tags=["feedback"])


@router.post("/long-feedback", response_model=LongFeedbackResponse)
async def long_feedback(req: LongFeedbackRequest, scenario: ScenarioDep) -> LongFeedbackResponse:
    await apply_scenario(scenario)

    blocked = scenario is Scenario.BLOCKED or find_violation(*req.daily_summaries) is not None
    return runner.long_feedback(req, scenario, blocked=blocked)
