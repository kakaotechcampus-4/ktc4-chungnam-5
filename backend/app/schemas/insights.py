"""insights(장기 피드백) API 요청/응답 스키마."""

import enum

from app.schemas.base import CamelModel

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


class InsightRefreshResponse(CamelModel):
    """POST /insights/long-term/refresh 202 응답."""

    status: InsightStatus
    poll_interval_ms: int = INSIGHT_REFRESH_POLL_INTERVAL_MS
