"""AI 스텁 서버.

AI 레이어가 아직 없어서, BE 가 개발하는 동안 요청을 받아 고정된 더미 응답을 돌려준다.
실제 모델은 부르지 않는다.

    uvicorn main:app --reload --port 8001
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status

import fixtures
from schemas import (
    AnalyzeMealRequest,
    AnalyzeMealResponse,
    LongFeedbackRequest,
    LongFeedbackResponse,
    ShortFeedbackRequest,
    ShortFeedbackResponse,
)


class Scenario(str, Enum):
    """`X-Stub-Scenario` 헤더로 고른다. 없으면 SUCCESS."""

    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"  # safetyStatus=BLOCKED → BE 는 medical_handoff_logs 에 기록
    ERROR_500 = "ERROR_500"  # 5xx → Worker 재시도 → DLQ → status=FAILED


def resolve_scenario(x_stub_scenario: Annotated[str | None, Header()] = None) -> Scenario:
    if x_stub_scenario is None:
        return Scenario.SUCCESS
    try:
        scenario = Scenario(x_stub_scenario.strip().upper())
    except ValueError:
        known = ", ".join(s.value for s in Scenario)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"알 수 없는 X-Stub-Scenario: {x_stub_scenario}. 가능한 값: {known}",
        ) from None

    if scenario is Scenario.ERROR_500:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="스텁이 의도적으로 낸 오류 (X-Stub-Scenario: ERROR_500)",
        )
    return scenario


ScenarioDep = Annotated[Scenario, Depends(resolve_scenario)]

app = FastAPI(
    title="AI Service (stub)",
    version="0.1.0",
    description=(
        "AI 레이어 더미. 고정 응답을 돌려준다.\n\n"
        "`X-Stub-Scenario` 헤더: `SUCCESS`(기본) · `BLOCKED` · `ERROR_500`"
    ),
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze-meal", response_model=AnalyzeMealResponse, tags=["analyze"])
def analyze_meal(req: AnalyzeMealRequest, scenario: ScenarioDep) -> AnalyzeMealResponse:
    if scenario is Scenario.BLOCKED:
        return fixtures.analyze_meal_blocked(req.meal_id)
    return fixtures.analyze_meal(req.meal_id)


@app.post("/short-feedback", response_model=ShortFeedbackResponse, tags=["feedback"])
def short_feedback(req: ShortFeedbackRequest, scenario: ScenarioDep) -> ShortFeedbackResponse:
    if scenario is Scenario.BLOCKED:
        return fixtures.short_feedback_blocked()
    return fixtures.short_feedback(req.scope)


@app.post("/long-feedback", response_model=LongFeedbackResponse, tags=["feedback"])
def long_feedback(req: LongFeedbackRequest, scenario: ScenarioDep) -> LongFeedbackResponse:
    if scenario is Scenario.BLOCKED:
        return fixtures.long_feedback_blocked()
    return fixtures.long_feedback()
