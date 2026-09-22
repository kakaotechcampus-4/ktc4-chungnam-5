"""컨디션 기록 공개 API.

경로에 /api/v1 을 쓰지 않는다 — api/v1/__init__.py 의 api_router 가 prefix 로 갖고 있다.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.user_state import UserStateCreateRequest, UserStateResponse
from app.services import user_state as user_state_service

router = APIRouter()


@router.post(
    "/user-states",
    response_model=ApiResponse[UserStateResponse],
    status_code=201,
    responses=error_responses(401, 404, 422),
)
def create_user_state(
    payload: UserStateCreateRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[UserStateResponse]:
    """오늘 컨디션(체중·식욕·위장관 증상) 기록."""
    return ok(
        user_state_service.create_user_state(db, user_id=user_id, request=payload)
    )


@router.get(
    "/user-states/latest",
    response_model=ApiResponse[UserStateResponse],
    responses=error_responses(401, 404),
)
def get_latest_user_state(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[UserStateResponse]:
    """가장 최근 컨디션 기록 조회 (홈 컨디션 기록 팝업 미리 채우기).

    기록이 하나도 없으면 404 가 아니라 200 + data: null 이다.
    """
    return ok(user_state_service.get_latest_user_state(db, user_id=user_id))
