"""사용자 프로필 도메인 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 커밋 시점은 여기서 정한다 —
"유저 생성 + 첫 체중 기록" 이 한 트랜잭션이어야 하기 때문이다.

체중은 users 에 없다. user_states 가 유일한 출처이고, 프로필 응답의 weightKg 는
가장 최근 기록에서 읽는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.errors import ApiError, ErrorCode
from app.crud import medication as medication_crud
from app.crud import user as user_crud
from app.crud import user_state as user_state_crud
from app.models.user import User
from app.schemas.user import (
    OnboardingStatus,
    ProfileCreatedResponse,
    ProfileCreateRequest,
    ProfileUpdateRequest,
    UserProfileResponse,
)


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _resolve_onboarding_status(db: Session, user: User) -> OnboardingStatus:
    """진입 화면 판정.

    height_cm 이 비어 있으면 프로필이 아직 안 채워진 것으로 본다. 인증이 붙기
    전에는 프로필 생성이 곧 유저 생성이라 이 분기가 나오지 않지만, 카카오 로그인이
    붙으면 바로 쓰인다.
    """
    if user.height_cm is None:
        return OnboardingStatus.PROFILE_REQUIRED
    if medication_crud.get_current(db, user.id) is None:
        return OnboardingStatus.MEDICATION_REQUIRED
    return OnboardingStatus.READY


def _build_profile(db: Session, user: User) -> UserProfileResponse:
    return UserProfileResponse(
        user_id=user.id,
        nickname=user.nickname,
        height_cm=_to_float(user.height_cm),
        weight_kg=_to_float(user_state_crud.get_latest_weight(db, user.id)),
        baseline_intake=float(user.baseline_meal_kcal),
        onboarding_status=_resolve_onboarding_status(db, user),
    )


def _get_user_or_raise(db: Session, user_id: uuid.UUID) -> User:
    user = user_crud.get(db, user_id)
    if user is None:
        raise ApiError(ErrorCode.USER_NOT_FOUND, "사용자를 찾을 수 없습니다.", 404)
    return user


def create_profile(
    db: Session, *, request: ProfileCreateRequest
) -> ProfileCreatedResponse:
    """사용자를 만들고 첫 체중을 user_states 에 남긴다."""
    user = user_crud.create(
        db,
        nickname=request.nickname,
        height_cm=request.height_cm,
        baseline_meal_kcal=request.baseline_intake,
    )
    user_state_crud.create(
        db,
        user_id=user.id,
        weight_kg=request.weight_kg,
        recorded_at=datetime.now(timezone.utc),
    )
    db.commit()
    db.refresh(user)

    profile = _build_profile(db, user)
    return ProfileCreatedResponse(**profile.model_dump(), created_at=user.created_at)


def get_me(db: Session, *, user_id: uuid.UUID) -> UserProfileResponse:
    return _build_profile(db, _get_user_or_raise(db, user_id))


def update_me(
    db: Session, *, user_id: uuid.UUID, request: ProfileUpdateRequest
) -> UserProfileResponse:
    """준 필드만 바꾼다. 체중은 users 를 고치지 않고 새 기록을 남긴다."""
    user = _get_user_or_raise(db, user_id)

    user_crud.update(
        db,
        user,
        nickname=request.nickname,
        height_cm=request.height_cm,
        baseline_meal_kcal=request.baseline_intake,
    )
    if request.weight_kg is not None:
        user_state_crud.create(
            db,
            user_id=user.id,
            weight_kg=request.weight_kg,
            recorded_at=datetime.now(timezone.utc),
        )
    db.commit()
    db.refresh(user)

    return _build_profile(db, user)
