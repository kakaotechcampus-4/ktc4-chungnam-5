"""`users` 접근.

목표는 `user_goal.py`, 상태 기록은 `user_state.py` 에 있다 — 담당이 갈리는 테이블이라 나눴다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User


def get(db: Session, user_id: uuid.UUID | str) -> User | None:
    return db.get(User, user_id)


def get_by_provider(db: Session, auth_provider: str, provider_user_id: str) -> User | None:
    """OAuth 계정 식별 경로.

    이메일로 찾지 않는다 — 카카오는 이메일이 선택 동의라 없을 수 있다.
    """
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
    """토큰과 갱신 시각은 같이 움직인다. 따로 건드리면 둘이 어긋난다."""
    user.fcm_token = token
    user.fcm_token_updated_at = dt.datetime.now(dt.timezone.utc) if token else None
    db.add(user)
