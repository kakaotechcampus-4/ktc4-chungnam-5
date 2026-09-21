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
