"""user_states 테이블 접근.

체중의 유일한 출처다. users 테이블에는 체중 컬럼이 없다.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import UserState


def create(
    db: Session,
    *,
    user_id: uuid.UUID,
    weight_kg: Decimal | None,
    recorded_at: datetime,
    appetite_level: int | None = None,
    gi_symptoms: list | None = None,
    note: str | None = None,
) -> UserState:
    state = UserState(
        user_id=user_id,
        weight_kg=weight_kg,
        recorded_at=recorded_at,
        appetite_level=appetite_level,
        gi_symptoms=gi_symptoms if gi_symptoms is not None else [],
        note=note,
    )
    db.add(state)
    db.flush()
    return state


def get_latest_weight(db: Session, user_id: uuid.UUID) -> Decimal | None:
    """가장 최근에 '체중이 적힌' 기록의 체중. 없으면 None.

    체중 없이 증상만 기록한 행이 있을 수 있어서 NULL 을 걸러낸다.
    인덱스 ix_user_states_user_id_recorded_at 을 탄다.
    """
    stmt = (
        select(UserState.weight_kg)
        .where(UserState.user_id == user_id, UserState.weight_kg.is_not(None))
        .order_by(UserState.recorded_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()
