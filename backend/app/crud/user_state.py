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


def get_latest_weight_before(
    db: Session, *, user_id: uuid.UUID, before: datetime
) -> Decimal | None:
    """before 보다 앞선, 체중이 적힌 내 기록 중 가장 최근 것의 체중. 없으면 None.

    recorded_at 을 함수로 감싸지 않아 ix_user_states_user_id_recorded_at 범위 탐색을 탄다.
    경계(before)를 어떻게 잡을지는 호출하는 쪽이 정한다.
    """
    stmt = (
        select(UserState.weight_kg)
        .where(
            UserState.user_id == user_id,
            UserState.weight_kg.is_not(None),
            UserState.recorded_at < before,
        )
        .order_by(UserState.recorded_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_weight_at(db: Session, *, user_id: uuid.UUID, at: datetime) -> Decimal | None:
    """at 시점의 체중 — at 이전 마지막 기록, 없으면 가장 최근 기록. 둘 다 없으면 None.

    끼니 채점(`services/evaluation::_weight_at`)과 끼니 피드백 AI 요청
    (`worker/jobs/feedback_meal.py`)이 같은 값을 써야 해서 규칙을 여기 한 곳에 둔다 —
    AI 가 Quality 점수의 근거와 다른 체중을 보고 문장을 쓰면 안 된다.

    대신하는 경우는 가입 직후 과거 시각으로 식사를 적었을 때다 — 그때 체중이 없다고
    비우면 온보딩에서 받은 체중이 있는데도 쓰지 못한다.
    """
    weight = get_latest_weight_before(db, user_id=user_id, before=at)
    if weight is None:
        weight = get_latest_weight(db, user_id)
    return weight


def get_latest(db: Session, *, user_id: uuid.UUID) -> UserState | None:
    """recorded_at 이 가장 늦은 내 기록 1건. 없으면 None.

    체중으로 거르지 않는다 (get_latest_weight 와 다르다). recorded_at 동률이면
    created_at 이 늦은 것, 그래도 같으면 id 순으로 결정성만 확보한다.
    """
    stmt = (
        select(UserState)
        .where(UserState.user_id == user_id)
        .order_by(
            UserState.recorded_at.desc(),
            UserState.created_at.desc(),
            UserState.id.desc(),
        )
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()
