"""`user_goals` 접근. **진행 중인 목표는 사용자당 하나다**(부분 유니크 인덱스)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import GoalStatus
from app.models.user import UserGoal


def get_active(db: Session, user_id: uuid.UUID | str) -> UserGoal | None:
    return db.scalar(
        select(UserGoal).where(
            UserGoal.user_id == user_id,
            UserGoal.status == GoalStatus.ACTIVE,
        )
    )


def add(db: Session, user_id: uuid.UUID | str, target_weight_kg: Decimal) -> UserGoal:
    """새 목표.

    진행 중인 목표가 이미 있으면 `uq_user_goals_active` 에 걸린다. 먼저 `close` 로
    닫아야 한다 — 목표가 둘이면 진행률을 무엇 대비로 보여줄지 모호해진다.
    """
    goal = UserGoal(user_id=user_id, target_weight_kg=target_weight_kg)
    db.add(goal)
    return goal


def close(db: Session, goal: UserGoal, status: GoalStatus) -> None:
    goal.status = status
    db.add(goal)
