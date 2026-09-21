"""user_states 테이블 접근.

체중의 유일한 출처다. users 테이블에는 체중 컬럼이 없다.
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user import UserState

_KST = "Asia/Seoul"
_KST_ZONE = ZoneInfo(_KST)


def _kst_day(column):
    """recorded_at(UTC 로 저장됨)을 KST 기준 '그 날' 로 자른다.

    crud/meal.py 의 _kst_day 와 같은 식이다. 그쪽은 GET /dashboard 작업에서 이름이
    바뀌는 중이라 충돌을 피하려고 여기 따로 둔다 — 머지 후 한 곳으로 합친다.
    """
    return func.date_trunc("day", func.timezone(_KST, column))


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


def get_last_week_weight(
    db: Session, *, user_id: uuid.UUID, recorded_at: datetime
) -> Decimal | None:
    """recorded_at 기준 "지난주" 비교 대상 체중. 없으면 None.

    비교 대상 = KST 날짜가 (recorded_at 의 KST 날짜 − 7일) 이하인, 체중이 적힌 내 기록 중
    가장 최근 것. 날짜 단위로 자르므로 경계 날에도 그날 마지막 기록이 대표값이 된다
    (GET /dashboard 의 하루 대표값 규칙과 같다). 기준은 서버 now 가 아니라 recorded_at 이다.
    """
    kst_date = recorded_at.astimezone(_KST_ZONE).date()
    cutoff_day = datetime.combine(kst_date - timedelta(days=7), datetime.min.time())
    stmt = (
        select(UserState.weight_kg)
        .where(
            UserState.user_id == user_id,
            UserState.weight_kg.is_not(None),
            _kst_day(UserState.recorded_at) <= cutoff_day,
        )
        .order_by(UserState.recorded_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()
