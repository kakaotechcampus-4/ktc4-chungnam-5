"""`user_states` 접근. 사용자가 입력한 체중·식욕·GI 증상."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import UserState


def get_latest(db: Session, user_id: uuid.UUID | str) -> UserState | None:
    return db.scalar(
        select(UserState)
        .where(UserState.user_id == user_id)
        .order_by(UserState.recorded_at.desc())
        .limit(1)
    )


def add(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    recorded_at: dt.datetime,
    appetite_level: int | None = None,
    weight_kg: Decimal | None = None,
    gi_symptoms: list | dict | None = None,
    note: str | None = None,
) -> UserState:
    state = UserState(
        user_id=user_id,
        recorded_at=recorded_at,
        appetite_level=appetite_level,
        weight_kg=weight_kg,
        gi_symptoms=gi_symptoms if gi_symptoms is not None else [],
        note=note,
    )
    db.add(state)
    return state
