"""`daily_feedbacks` 접근. **하루당 1행이다**(`user_id`, `feedback_date` UNIQUE)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SafetyStatus
from app.models.feedback import DailyFeedback, DailyFeedbackSource, MealFeedback


def get(db: Session, user_id: uuid.UUID | str, day: dt.date) -> DailyFeedback | None:
    return db.scalar(
        select(DailyFeedback).where(
            DailyFeedback.user_id == user_id,
            DailyFeedback.feedback_date == day,
        )
    )


def upsert(
    db: Session,
    *,
    user_id: uuid.UUID | str,
    feedback_date: dt.date,
    summary: str | None,
    quantity_score: Decimal | None,
    quality_score: Decimal | None,
    satiety_score: Decimal | None,
    model_version: str | None,
    safety_status: SafetyStatus = SafetyStatus.REVIEW_REQUIRED,
    sources: list[MealFeedback] | None = None,
) -> DailyFeedback:
    """점수 셋은 AI 가 아니라 BE 가 집계한 값이다 (규칙 2).

    `sources` 를 주면 근거 링크를 통째로 갈아 끼운다 — 재생성 때 옛 근거가 섞이면
    "이 요약이 어디서 나왔나" 추적이 틀린다.
    """
    row = get(db, user_id, feedback_date)
    if row is None:
        row = DailyFeedback(user_id=user_id, feedback_date=feedback_date)
        db.add(row)

    row.summary = summary
    row.quantity_score = quantity_score
    row.quality_score = quality_score
    row.satiety_score = satiety_score
    row.model_version = model_version
    row.safety_status = safety_status

    if sources is not None:
        row.sources = [DailyFeedbackSource(meal_feedback_id=src.id) for src in sources]
    return row
