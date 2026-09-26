"""long_term_feedbacks 조회, task_queue 의 feedback.long 작업 조회."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import FeedbackPeriodType
from app.models.feedback import LongTermFeedback
from app.models.task import Task

REFRESH_TASK_TYPE = "feedback.long"


def get_latest(
    db: Session, *, user_id: uuid.UUID, period_type: FeedbackPeriodType
) -> LongTermFeedback | None:
    """이 유형의 가장 최근 장기 피드백 한 행. 없으면 None."""
    return db.execute(
        select(LongTermFeedback)
        .where(
            LongTermFeedback.user_id == user_id,
            LongTermFeedback.period_type == period_type,
        )
        .order_by(LongTermFeedback.period_start.desc(), LongTermFeedback.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_latest_refresh_task(
    db: Session, *, user_id: uuid.UUID, period_type: FeedbackPeriodType
) -> Task | None:
    """이 유형의 feedback.long 작업 중 가장 최근에 등록된 것. 없으면 None.

    payload 키 이름(userId/periodType)은 `worker/jobs/feedback_long.py` 가 이미
    정해둔 것과 맞춘다 — 여기서 새로 정하면 워커가 못 읽는다.
    """
    return db.execute(
        select(Task)
        .where(
            Task.type == REFRESH_TASK_TYPE,
            Task.payload["userId"].astext == str(user_id),
            Task.payload["periodType"].astext == period_type.value,
        )
        .order_by(Task.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
