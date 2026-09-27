"""하루 피드백 공개 API.

경로에 /api/v1 을 쓰지 않는다 — api/v1/__init__.py 의 api_router 가 prefix 로 갖고 있다.
insights.py 는 장기 피드백 담당이라 하루 피드백은 이 파일에 둔다.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.response import ApiResponse, error_responses
from app.db.session import get_db
from app.schemas.daily_feedbacks import DailyFeedbackRefreshRequest
from app.schemas.insights import InsightRefreshResponse

router = APIRouter()


@router.post(
    "/insights/daily/refresh",
    response_model=ApiResponse[InsightRefreshResponse],
    status_code=202,
    responses=error_responses(401, 404, 422),
)
def refresh_daily_feedback(
    body: DailyFeedbackRefreshRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[InsightRefreshResponse]:
    """그날 하루 피드백 재생성 요청. 작업만 등록하고 202 를 돌려준다."""
    raise NotImplementedError("refresh_daily_feedback 미구현")
