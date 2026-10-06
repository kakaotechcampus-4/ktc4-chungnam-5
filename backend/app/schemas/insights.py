"""insights(장기 피드백) API 요청/응답 스키마."""

import enum
from datetime import date
from typing import Literal

from app.models.enums import FeedbackStatus
from app.schemas.base import CamelModel, KstDatetime

INSIGHT_REFRESH_POLL_INTERVAL_MS = 1500

# GET /insights/long-term 의 feedbackStatus 는 이 리스트에서 고른다. 자체 enum 을 새로
# 만들지 않고 `services/evaluation`의 MealConfirmResponse.feedback_status 와 같은
# `FeedbackStatus`(PENDING/GENERATING/READY/FAILED)를 재사용한다 — "피드백 문장이
# 아직 없다"는 같은 개념이라 명세도 같은 열거형을 쓴다(PR #46 리뷰).
#
# `long_term_feedbacks` 행이 아예 없으면 PENDING, task_queue 에 대기 중인
# feedback.long 작업이 있으면 GENERATING, 성공한 행이 있으면(낡았어도) READY,
# 성공한 행 없이 마지막 시도가 실패로 끝났으면 FAILED.


class StaleReason(str, enum.Enum):
    """생성 시점 이후 무엇이 바뀌어서 낡은 정보가 됐는지."""

    MEAL_DELETED = "MEAL_DELETED"
    MEAL_EDITED = "MEAL_EDITED"
    STAGE_CHANGED = "STAGE_CHANGED"
    NEW_MEALS = "NEW_MEALS"


class InsightPeriod(CamelModel):
    from_: date | None
    to: date


class LongTermInsightResponse(CamelModel):
    """GET /insights/long-term 응답."""

    period: InsightPeriod
    feedback_status: FeedbackStatus
    data_sufficient: bool
    trend_summary: str | None
    recommendation: str | None
    generated_at: KstDatetime | None
    stale: bool
    stale_reason: StaleReason | None


class InsightRefreshRequest(CamelModel):
    """POST /insights/long-term/refresh 요청."""

    period: Literal["7d", "28d", "all"]


class InsightRefreshResponse(CamelModel):
    """POST /insights/long-term/refresh · /insights/daily/refresh 202 응답."""

    feedback_status: FeedbackStatus
    poll_interval_ms: int = INSIGHT_REFRESH_POLL_INTERVAL_MS
