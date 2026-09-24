"""끼니 피드백 · 사후 포만감 API.

이 층이 하는 일은 넷뿐이다 — 인증 의존성으로 유저 확인, 요청 검증, 서비스 호출,
도메인 예외를 HTTP 로 번역. 성분 조회도 마스킹도 여기 없다.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.errors import ApiError, ErrorCode
from app.core.response import ApiResponse, error_responses, ok, ok_with_code
from app.db.session import get_db
from app.models.enums import SafetyStatus
from app.schemas.feedback import MealFeedbackResponse
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
        view = feedback_service.get_feedback(db, user_id=user_id, meal_id=meal_id)
    except feedback_service.MealNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, str(exc), 404) from None

    # 명세 에러표: `MEDICAL_QUESTION_DETECTED` | 200 | "상담 안내 후 복귀".
    # `safetyStatus` 로도 분기는 되지만, 이 레포는 FE 가 `error.code` 로 분기하는 것을
    # 전제로 200 에 도메인 코드를 싣는다(`evaluations.py` 의 `NUTRITION_NOT_MATCHED`
    # 와 같은 모양). 여기만 빼면 같은 성격의 분기가 두 방식이 된다.
    if view.safety_status is SafetyStatus.BLOCKED:
        return ok_with_code(
            view,
            ErrorCode.MEDICAL_QUESTION_DETECTED,
            "의료 판단이 필요한 내용이라 피드백 대신 상담을 안내해요.",
        )
    return ok(view)
