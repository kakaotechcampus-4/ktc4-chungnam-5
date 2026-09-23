"""insights(장기 피드백) API 요청/응답 스키마."""

import enum
from datetime import date
from typing import Literal

from app.schemas.base import CamelModel, KstDatetime

INSIGHT_REFRESH_POLL_INTERVAL_MS = 1500


class InsightStatus(str, enum.Enum):
    """GET /insights/long-term 의 상태. DB 컬럼이 아니라 파생값이다.

    `long_term_feedbacks` 행이 아예 없으면 NOT_GENERATED, task_queue 에 대기 중인
    feedback.long 작업이 있으면 GENERATING, 성공한 행이 있으면(낡았어도) READY,
    성공한 행 없이 마지막 시도가 실패로 끝났으면 FAILED.
    """

    NOT_GENERATED = "NOT_GENERATED"
    GENERATING = "GENERATING"
    READY = "READY"
    FAILED = "FAILED"


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
    status: InsightStatus
    data_sufficient: bool
    trend_summary: str | None
    recommendation: str | None
    generated_at: KstDatetime | None
    stale: bool
    stale_reason: StaleReason | None


class InsightRefreshRequest(CamelModel):
    """POST /insights/long-term/refresh 요청.

    period=all 은 이번 범위에서 미지원(GET 과 동일한 이유) — 값 자체를 좁혀서
    FastAPI 가 422 로 걸러내게 한다.
    """

    period: Literal["7d", "28d"]


class InsightRefreshResponse(CamelModel):
    """POST /insights/long-term/refresh 202 응답."""

    status: InsightStatus
    poll_interval_ms: int = INSIGHT_REFRESH_POLL_INTERVAL_MS
