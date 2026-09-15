"""users 테이블 접근. 여기 말고는 아무도 User 를 직접 쿼리하지 않는다.

commit 하지 않는다 — 커밋 시점은 services/ 가 정한다.
"""

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.user import User


def create(
    db: Session,
    *,
    nickname: str,
    height_cm: Decimal | None,
    baseline_meal_kcal: Decimal,
) -> User:
    """새 사용자를 만든다. flush 까지만 하고 id 가 채워진 객체를 돌려준다."""
    user = User(
        nickname=nickname,
        height_cm=height_cm,
        baseline_meal_kcal=baseline_meal_kcal,
    )
    db.add(user)
    db.flush()
    return user


def get(db: Session, user_id: uuid.UUID) -> User | None:
    return db.get(User, user_id)


def update(
    db: Session,
    user: User,
    *,
    nickname: str | None = None,
    height_cm: Decimal | None = None,
    baseline_meal_kcal: Decimal | None = None,
) -> User:
    """None 으로 들어온 필드는 건드리지 않는다 — PATCH 의미 그대로다."""
    if nickname is not None:
        user.nickname = nickname
    if height_cm is not None:
        user.height_cm = height_cm
    if baseline_meal_kcal is not None:
        user.baseline_meal_kcal = baseline_meal_kcal
    db.flush()
    return user
