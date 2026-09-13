"""`satiety_logs` 접근. **식사당 1행이다**(`meal_id` UNIQUE)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.meal import SatietyLog


def get(db: Session, meal_id: uuid.UUID | str) -> SatietyLog | None:
    return db.scalar(select(SatietyLog).where(SatietyLog.meal_id == meal_id))


def upsert(
    db: Session,
    meal_id: uuid.UUID | str,
    *,
    logged_at: dt.datetime,
    satiety_before: int | None = None,
    satiety_after: int | None = None,
    hunger_return_minutes: int | None = None,
    user_comment: str | None = None,
) -> SatietyLog:
    """식전에 한 번, 식후에 한 번 채워진다. 그래서 upsert 다.

    `None` 인 인자는 건드리지 않는다 — 식후 입력이 식전 값을 지우면 안 된다.
    """
    row = get(db, meal_id)
    if row is None:
        row = SatietyLog(meal_id=meal_id, logged_at=logged_at)
        db.add(row)

    row.logged_at = logged_at
    if satiety_before is not None:
        row.satiety_before = satiety_before
    if satiety_after is not None:
        row.satiety_after = satiety_after
    if hunger_return_minutes is not None:
        row.hunger_return_minutes = hunger_return_minutes
    if user_comment is not None:
        row.user_comment = user_comment
    return row
