"""하루 피드백(daily_feedbacks) API 요청/응답 스키마.

refresh 응답(202)은 장기 피드백과 같은 모양이라 schemas/insights.py 의 InsightRefreshResponse 를 쓴다.
"""

import uuid
from datetime import date

from pydantic import ConfigDict

from app.models.enums import FeedbackStatus, SafetyStatus
from app.schemas.base import CamelModel, KstDatetime
from app.schemas.insights import StaleReason


class DailyFeedbackRefreshRequest(CamelModel):
    """POST /insights/daily/refresh 요청.

    extra="forbid": 오타난 필드(예: dat)를 조용히 무시하지 않는다 —
    schemas/user_state.py UserStateCreateRequest 와 같은 이유.
    """

    model_config = ConfigDict(extra="forbid")

    date: date


class DailyFeedbackScores(CamelModel):
    """하루 Q/Q/S 점수 (0~100 정수). 값이 없는 축은 None."""

    quantity: int | None
    quality: int | None
    satiety: int | None


class DailyFeedbackResponse(CamelModel):
    """GET /insights/daily 응답.

    그날 행이 없으면 daily_feedback_id · summary · scores · safety_status · generated_at ·
    stale_reason 은 None, source_meal_ids 는 [], stale 은 False 다.
    summary 는 safety_status 가 SAFE 일 때만 채운다.
    """

    daily_feedback_id: uuid.UUID | None
    feedback_date: date
    feedback_status: FeedbackStatus
    summary: str | None
    scores: DailyFeedbackScores | None
    source_meal_ids: list[uuid.UUID]
    safety_status: SafetyStatus | None
    generated_at: KstDatetime | None
    stale: bool
    stale_reason: StaleReason | None
