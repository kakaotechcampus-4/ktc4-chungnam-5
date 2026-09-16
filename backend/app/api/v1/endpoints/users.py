"""사용자 프로필 공개 API.

경로에 /api/v1 을 쓰지 않는다 — api/v1/__init__.py 의 api_router 가 prefix 로 갖고 있다.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, ok
from app.db.session import get_db
from app.schemas.user import (
    ProfileCreatedResponse,
    ProfileCreateRequest,
    ProfileUpdateRequest,
    UserProfileResponse,
)
from app.services import user as user_service

router = APIRouter()


@router.post(
    "/users/profile",
    response_model=ApiResponse[ProfileCreatedResponse],
    status_code=201,
)
def create_profile(
    payload: ProfileCreateRequest,
    db: Session = Depends(get_db),
) -> ApiResponse[ProfileCreatedResponse]:
    """프로필 최초 등록. 인증이 없는 지금은 이 호출이 곧 사용자 생성이다."""
    return ok(user_service.create_profile(db, request=payload))


@router.get("/users/me", response_model=ApiResponse[UserProfileResponse])
def get_me(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[UserProfileResponse]:
    return ok(user_service.get_me(db, user_id=user_id))


@router.patch("/users/me", response_model=ApiResponse[UserProfileResponse])
def update_me(
    payload: ProfileUpdateRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[UserProfileResponse]:
    return ok(user_service.update_me(db, user_id=user_id, request=payload))
