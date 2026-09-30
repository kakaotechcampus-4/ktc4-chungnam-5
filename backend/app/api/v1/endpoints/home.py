"""홈 공개 API.

경로에 /api/v1 을 쓰지 않는다 — api/v1/__init__.py 의 api_router 가 prefix 로 갖고 있다.

services/ 끼리 import 하지 않으므로(규칙 5) 투약 서비스와 홈 서비스의 조합은 이 층에서 한다.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.errors import ApiError, ErrorCode
from app.core.response import ApiResponse, error_responses, ok
from app.core.time import now_kst
from app.db.session import get_db
from app.schemas.home import HomeResponse
from app.services import home as home_service
from app.services import medication as medication_service

router = APIRouter()


@router.get(
    "/home",
    response_model=ApiResponse[HomeResponse],
    summary="홈 — 투약 상태 · 위 게이지 · 오늘의 식사",
    # 422 는 X-User-Id 헤더 때문에 FastAPI 가 자동 생성한다 — medications.py 의 같은 주석 참고.
    responses=error_responses(401, 404, 422),
)
def get_home(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[HomeResponse]:
    """홈 화면이 한 번에 필요한 데이터.

    medication 은 GET /medications/current 와 같은 계산이다 — 투약 미등록이면 409 가 아니라
    `medication: null` 로 200 이다. 기준 식사가 없으면 `stomach: null` 이다.
    없는 사용자는 404 `USER_NOT_FOUND` 다. 읽기 전용.
    """
    # 시계는 여기서 한 번만 읽는다 — 투약의 "오늘"과 홈의 "오늘"·"몇 분 전"이 같은 순간이어야 한다.
    now = now_kst()

    # 투약은 GET /medications/current 의 계산을 그대로 쓴다. 미등록(STAGE_NOT_SET)은 홈에서는
    # 에러가 아니라 "카드 대신 입력 안내"라 None 으로 흡수한다. 없는 사용자 404 등은 그대로 올린다.
    try:
        medication = medication_service.get_current_view(db, user_id, today=now.date())
    except ApiError as exc:
        if exc.code is not ErrorCode.STAGE_NOT_SET:
            raise
        medication = None

    return ok(home_service.get_home(db, user_id=user_id, medication=medication, now=now))
