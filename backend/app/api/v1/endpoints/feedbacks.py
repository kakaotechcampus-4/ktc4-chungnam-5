"""끼니 피드백 · 사후 포만감 API.

이 층이 하는 일은 넷뿐이다 — 인증 의존성으로 유저 확인, 요청 검증, 서비스 호출,
도메인 예외를 HTTP 로 번역. 성분 조회도 마스킹도 여기 없다.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.errors import ApiError, ErrorCode
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.feedback import (
    MealFeedbackResponse,
    SatietyCheckinRequest,
    SatietyCheckinResponse,
)
from app.services import feedback as feedback_service

router = APIRouter()


@router.get(
    "/meals/{meal_id}/feedback",
    response_model=ApiResponse[MealFeedbackResponse],
    summary="단기 피드백 + 다음 끼니 추천",
    # 422 를 뺄 수 없다. body · query 가 없어도 FastAPI 가 `X-User-Id` 헤더를
    # 파라미터로 세어 422 응답을 자동 생성하고, 그게 `main.py` 가 지우는
    # `HTTPValidationError` 를 가리킨다 — 명시하지 않으면 openapi.json 에 깨진
    # $ref 가 남는다 (`test_no_route_references_the_removed_validation_schema`).
    responses=error_responses(401, 404, 422),
)
def get_meal_feedback(
    meal_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MealFeedbackResponse]:
    """이 끼니에 대한 AI 피드백과 다음 끼니 제안.

    없는 식사 · 남의 식사 · 삭제된 식사는 전부 404 로 같게 응답한다
    (`DELETE /meals/{mealId}` 와 같은 이유 — 소유권 누출 방지).

    **아직 피드백이 없어도 200 이다** — `feedbackStatus: PENDING`. 에러가 아니라
    "아직" 이고, 404 로 내리면 FE 가 "없는 식사" 와 구분하지 못한다.

    `safetyStatus` 가 `SAFE` 가 아니면 `summary` · `suggestions` 가 비어 나간다
    (명세: "상담 안내로 대체"). FE 는 그 필드를 보고 안내 화면으로 바꾼다.
    """
    try:
        return ok(
            feedback_service.get_feedback(db, user_id=user_id, meal_id=meal_id)
        )
    except feedback_service.MealNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, str(exc), 404) from None


@router.post(
    "/meals/{meal_id}/satiety-checkins",
    response_model=ApiResponse[SatietyCheckinResponse],
    status_code=status.HTTP_201_CREATED,
    summary="사후 포만감",
    responses=error_responses(401, 404, 422),
)
def add_satiety_checkin(
    meal_id: uuid.UUID,
    request: SatietyCheckinRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[SatietyCheckinResponse]:
    """식후 몇 시간 뒤의 포만감을 남긴다.

    **같은 시점을 다시 보내면 덮는다.** "식후 3시간 포만감" 은 하나이고, 더블탭이
    그래프에 점 두 개를 만들면 안 된다. 그래서 새로 만들든 고치든 201 이다 —
    사용자에게는 "이 시점의 기록이 생겼다" 로 같다.

    **상태를 보지 않는다.** 포만감은 확정 여부와 무관하게 실제로 겪는 일이고,
    명세에도 상태 조건이 없다.
    """
    try:
        return ok(
            feedback_service.add_checkin(
                db, user_id=user_id, meal_id=meal_id, request=request
            )
        )
    except feedback_service.MealNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, str(exc), 404) from None
