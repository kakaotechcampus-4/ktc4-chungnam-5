"""long_term_feedbacks 조회, task_queue 의 feedback.long 작업 조회."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import FeedbackPeriodType, SafetyStatus
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


def get_latest_safe(db: Session, user_id: uuid.UUID) -> LongTermFeedback | None:
    """챗봇 컨텍스트용 — 가장 최근에 갱신된, 보여 줘도 되는 장기 피드백.

    SAFE 이고 `trend_summary` 가 공백이 아닐 때만이다. 장기 피드백 화면에서 안 보이는
    문장을 챗봇이 AI 에 되먹이면 안 된다 (규칙 1).
    """
    stmt = (
        select(LongTermFeedback)
        .where(
            LongTermFeedback.user_id == user_id,
            LongTermFeedback.safety_status == SafetyStatus.SAFE,
            func.length(func.btrim(LongTermFeedback.trend_summary)) > 0,
        )
        .order_by(LongTermFeedback.updated_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()
