"""`meal_feedbacks` 접근. **식사당 1행이다**(`meal_id` UNIQUE)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SafetyStatus
from app.models.feedback import MealFeedback


def get_by_meal(db: Session, meal_id: uuid.UUID | str) -> MealFeedback | None:
    return db.scalar(select(MealFeedback).where(MealFeedback.meal_id == meal_id))


def upsert(
    db: Session,
    *,
    user_id: uuid.UUID | str,
    meal_id: uuid.UUID | str,
    body: str | None,
    suggestions: str | None,
    reasoning: str | None,
    model_version: str | None,
    safety_status: SafetyStatus = SafetyStatus.REVIEW_REQUIRED,
) -> MealFeedback:
    """`safety_status` 기본값이 REVIEW_REQUIRED 인 건 의도된 것이다.

    부르는 쪽이 값을 빠뜨려도 피드백이 그대로 노출되지 않는다 —
    가드레일을 통과해야만 SAFE 가 된다(규칙 1).
    """
    row = get_by_meal(db, meal_id)
    if row is None:
        row = MealFeedback(user_id=user_id, meal_id=meal_id)
        db.add(row)

    row.body = body
    row.suggestions = suggestions
    row.reasoning = reasoning
    row.model_version = model_version
    row.safety_status = safety_status
    return row
