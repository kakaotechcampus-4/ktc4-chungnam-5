"""`long_term_feedbacks` 접근.

**기간당 1행이다**(`user_id`, `period_type`, `period_start` UNIQUE).
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import FeedbackPeriodType, SafetyStatus
from app.models.feedback import DailyFeedback, LongTermFeedback, LongTermFeedbackSource


def get(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    period_type: FeedbackPeriodType,
    period_start: dt.date,
) -> LongTermFeedback | None:
    return db.scalar(
        select(LongTermFeedback).where(
            LongTermFeedback.user_id == user_id,
            LongTermFeedback.period_type == period_type,
            LongTermFeedback.period_start == period_start,
        )
    )


def upsert(
    db: Session,
    *,
    user_id: uuid.UUID | str,
    period_type: FeedbackPeriodType,
    period_start: dt.date,
    period_end: dt.date,
    trend_summary: str | None,
    recommendation: str | None,
    chart_data: dict | None,
    model_version: str | None,
    safety_status: SafetyStatus = SafetyStatus.REVIEW_REQUIRED,
    sources: list[DailyFeedback] | None = None,
) -> LongTermFeedback:
    """`chart_data` 는 AI 가 아니라 BE 가 `qqs_evaluations` 를 집계해 만든다 (규칙 2)."""
    row = get(db, user_id, period_type=period_type, period_start=period_start)
    if row is None:
        row = LongTermFeedback(
            user_id=user_id, period_type=period_type, period_start=period_start
        )
        db.add(row)

    row.period_end = period_end
    row.trend_summary = trend_summary
    row.recommendation = recommendation
    row.chart_data = chart_data
    row.model_version = model_version
    row.safety_status = safety_status

    if sources is not None:
        row.sources = [LongTermFeedbackSource(daily_feedback_id=src.id) for src in sources]
    return row
