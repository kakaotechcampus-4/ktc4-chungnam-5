"""`meals` 접근."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.models.enums import MealStatus, MealType
from app.models.meal import Meal


def get(db: Session, meal_id: uuid.UUID | str) -> Meal | None:
    return db.get(Meal, meal_id)


def create(
    db: Session,
    *,
    user_id: uuid.UUID | str,
    medication_snapshot_id: uuid.UUID | str,
    meal_type: MealType,
    eaten_at: dt.datetime,
    image_key: str | None = None,
    raw_text: str | None = None,
) -> Meal:
    """상태는 ANALYZING 으로 시작한다(서버 기본값).

    `image_key` · `raw_text` 중 최소 하나는 있어야 한다. 둘 다 없으면
    CHECK 제약(`input_present`)에 걸려 커밋이 깨진다 — 분석할 게 없는 식사다.
    """
    meal = Meal(
        user_id=user_id,
        medication_snapshot_id=medication_snapshot_id,
        meal_type=meal_type,
        eaten_at=eaten_at,
        image_key=image_key,
        raw_text=raw_text,
    )
    db.add(meal)
    return meal


def set_status(db: Session, meal: Meal, status: MealStatus) -> None:
    meal.status = status
    db.add(meal)
