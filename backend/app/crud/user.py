"""`users` · `user_goals` · `user_states` 접근."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import GoalStatus
from app.models.user import User, UserGoal, UserState


# ─────────────────────────── users ───────────────────────────


def get(db: Session, user_id: uuid.UUID | str) -> User | None:
    return db.get(User, user_id)


def get_by_provider(db: Session, auth_provider: str, provider_user_id: str) -> User | None:
    """OAuth 로그인의 계정 식별 경로. 이메일은 선택 동의라 쓰지 않는다."""
    return db.scalar(
        select(User).where(
            User.auth_provider == auth_provider,
            User.provider_user_id == provider_user_id,
        )
    )


def create(
    db: Session,
    *,
    nickname: str,
    baseline_meal_kcal: Decimal,
    auth_provider: str | None = None,
    provider_user_id: str | None = None,
    email: str | None = None,
) -> User:
    user = User(
        nickname=nickname,
        baseline_meal_kcal=baseline_meal_kcal,
        auth_provider=auth_provider,
        provider_user_id=provider_user_id,
        email=email,
    )
    db.add(user)
    return user


def set_baseline(db: Session, user: User, baseline_meal_kcal: Decimal) -> None:
    """평소 한 끼 열량. Quantity 감소폭의 분모다 (D7)."""
    user.baseline_meal_kcal = baseline_meal_kcal
    db.add(user)


def set_fcm_token(db: Session, user: User, token: str | None) -> None:
    user.fcm_token = token
    user.fcm_token_updated_at = dt.datetime.now(dt.timezone.utc) if token else None
    db.add(user)


# ─────────────────────────── user_goals ───────────────────────────


def get_active_goal(db: Session, user_id: uuid.UUID | str) -> UserGoal | None:
    """진행 중인 목표는 사용자당 하나다(부분 유니크 인덱스)."""
    return db.scalar(
        select(UserGoal).where(
            UserGoal.user_id == user_id,
            UserGoal.status == GoalStatus.ACTIVE,
        )
    )


def add_goal(db: Session, user_id: uuid.UUID | str, target_weight_kg: Decimal) -> UserGoal:
    """새 목표를 만든다.

    진행 중인 목표가 이미 있으면 유니크 인덱스에 걸린다. 먼저 `close_goal` 로
    기존 목표를 닫아야 한다 — 목표가 둘이면 진행률을 무엇 대비로 보여줄지 모호해진다.
    """
    goal = UserGoal(user_id=user_id, target_weight_kg=target_weight_kg)
    db.add(goal)
    return goal


def close_goal(db: Session, goal: UserGoal, status: GoalStatus) -> None:
    goal.status = status
    db.add(goal)


# ─────────────────────────── user_states ───────────────────────────


def get_latest_state(db: Session, user_id: uuid.UUID | str) -> UserState | None:
    return db.scalar(
        select(UserState)
        .where(UserState.user_id == user_id)
        .order_by(UserState.recorded_at.desc())
        .limit(1)
    )


def list_states(
    db: Session,
    user_id: uuid.UUID | str,
    *,
    since: dt.datetime | None = None,
    until: dt.datetime | None = None,
) -> list[UserState]:
    stmt = select(UserState).where(UserState.user_id == user_id)
    if since is not None:
        stmt = stmt.where(UserState.recorded_at >= since)
    if until is not None:
        stmt = stmt.where(UserState.recorded_at <= until)
    return list(db.scalars(stmt.order_by(UserState.recorded_at.desc())).all())


def add_state(
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
