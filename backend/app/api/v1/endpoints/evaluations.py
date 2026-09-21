"""식사 확정·평가 조회 API.

이 층이 하는 일은 넷뿐이다 — 인증 의존성으로 유저 확인, 요청 검증, 서비스 호출,
도메인 예외를 HTTP 로 번역. 채점도 단계 판정도 여기 없다.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.errors import ApiError, ErrorCode
from app.core.response import ApiResponse, error_responses, ok, ok_with_code
from app.db.session import get_db
from app.schemas.evaluation import (
    MealConfirmRequest,
    MealConfirmResponse,
    MealEvaluationResponse,
)
from app.services import evaluation as evaluation_service

router = APIRouter()


def _respond(result: evaluation_service.EvaluationResult):
    """성분 합계가 불완전하면 200 안에 `NUTRITION_NOT_MATCHED` 를 실어 보낸다.

    이름 매칭에 실패했거나 양을 모르는 음식은 합산에서 빠진다. 숫자만 내보내면
    사용자는 적게 나온 단백질을 **완전한 값으로 믿고** 다음 끼니를 조절한다
   . 명세가 이 코드를 "200 응답 안의 플래그" 로 정의해 둔 자리다 —
    "해당 항목 직접 입력" 이 FE 의 처리다.

    실패가 아니라 단서다. `success` 는 참이고 `data` 도 그대로 있다.
    """
    if result.unmatched_items:
        return ok_with_code(
            result.view,
            ErrorCode.NUTRITION_NOT_MATCHED,
            f"영양정보를 찾지 못한 음식 {result.unmatched_items}개는 합계에서 빠졌어요.",
        )
    if result.items_without_amount:
        # 성분은 찾았고 **양**을 모른다. "영양정보를 찾지 못했다" 고 안내하면
        # 사용자가 영양정보 입력 화면으로 가서 아무리 넣어도 안 풀린다.
        #
        # ⚠️ 명세에 "양을 모른다" 전용 코드가 없다. `NUTRITION_NOT_MATCHED` 를
        #    그대로 쓰되 메시지로 실제 원인을 말한다 — FE 라우팅이 이 경우엔
        #    맞지 않으므로 코드 신설을 팀 안건으로 올릴 것.
        return ok_with_code(
            result.view,
            ErrorCode.NUTRITION_NOT_MATCHED,
            f"먹은 양을 알 수 없는 음식 {result.items_without_amount}개는 합계에서 "
            "빠졌어요. 양을 g 으로 고쳐 주세요.",
        )
    return ok(result.view)


@router.post(
    "/meals/{meal_id}/confirm",
    response_model=ApiResponse[MealConfirmResponse],
    summary="식사 확정 → Q·Q·S 채점",
    responses=error_responses(401, 404, 409, 422),
)
def confirm_meal(
    meal_id: uuid.UUID,
    request: MealConfirmRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MealConfirmResponse]:
    """사용자가 확인 화면에서 [확인] 을 누른다. 점수는 여기서 확정된다.

    없는 식사 · 남의 식사 · 삭제된 식사는 전부 404 로 같게 응답한다
    (`DELETE /meals/{mealId}` 와 같은 이유 — 소유권 누출 방지).

    확정할 수 없는 상태면 409 `CONFLICT` 다. 명세의 `NOT_CONFIRMED` 는 "아직 확정
    안 됨" 이라 조회 쪽 코드이고, 이쪽은 "지금은 확정 못 함" 이라 뜻이 다르다.

    **200 으로 즉답한다.** Q/Q/S 는 순수 함수라 비동기로 뺄 이유가 없다
    (절대 규칙 2). 명세의 `202 + 폴링` 은 `POST /meals` 자리다.
    """
    try:
        result = evaluation_service.confirm(
            db, user_id=user_id, meal_id=meal_id, request=request
        )
    except evaluation_service.MealNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, str(exc), 404) from None
    except evaluation_service.MealNotConfirmableError as exc:
        raise ApiError(ErrorCode.CONFLICT, str(exc), 409) from None

    return _respond(result)


@router.get(
    "/meals/{meal_id}/evaluation",
    response_model=ApiResponse[MealEvaluationResponse],
    summary="평가 재조회",
    responses=error_responses(401, 404, 409, 422),
)
def get_meal_evaluation(
    meal_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[MealEvaluationResponse]:
    """확정된 평가를 다시 본다. 응답은 confirm 과 같고 `feedbackStatus` 만 없다.

    확정 전 호출은 409 `NOT_CONFIRMED` 다 (명세). FE 는 이 코드를 보고 확인
    화면으로 되돌린다 — 에러 표의 "확인 화면으로 되돌림" 이 그 뜻이다.

    **다시 채점하지 않는다.** 확정 시점에 저장된 점수를 읽는다. 재채점하면
    기준선을 튜닝했을 때 같은 식사가 조회할 때마다 다른 점수를 낸다.
    """
    try:
        return _respond(
            evaluation_service.get_view(db, user_id=user_id, meal_id=meal_id)
        )
    except evaluation_service.MealNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, str(exc), 404) from None
    except evaluation_service.EvaluationNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_CONFIRMED, str(exc), 409) from None
