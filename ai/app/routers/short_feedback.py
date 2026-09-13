"""POST /short-feedback — 한 끼(scope=MEAL) 또는 하루(scope=DAILY) 코멘트."""

from __future__ import annotations

from fastapi import APIRouter

from agents import runner
from agents.schemas import ShortFeedbackRequest, ShortFeedbackResponse
from app.config import Scenario
from app.routers._deps import ScenarioDep, apply_scenario
from guardrail.medical import find_violation

router = APIRouter(tags=["feedback"])


def _free_text(req: ShortFeedbackRequest) -> list[str | None]:
    texts: list[str | None] = [meal.summary for meal in req.meals]
    if req.satiety:
        texts.append(req.satiety.user_comment)
    return texts


@router.post("/short-feedback", response_model=ShortFeedbackResponse)
async def short_feedback(req: ShortFeedbackRequest, scenario: ScenarioDep) -> ShortFeedbackResponse:
    await apply_scenario(scenario)

    blocked = scenario is Scenario.BLOCKED or find_violation(*_free_text(req)) is not None
    return runner.short_feedback(req, scenario, blocked=blocked)
