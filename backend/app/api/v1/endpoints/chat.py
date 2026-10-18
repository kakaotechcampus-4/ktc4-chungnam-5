"""챗봇(AI 코치) API (`contracts/ChatbotAPI.md`).

이 층이 하는 일은 넷뿐이다 — 인증 의존성으로 유저 확인, 요청 검증, 서비스 호출,
도메인 예외를 HTTP 로 번역.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.errors import ApiError, ErrorCode
from app.core.response import ApiResponse, error_responses, ok, ok_with_code
from app.db.session import get_db
from app.models.enums import SafetyStatus
from app.schemas.chat import ConversationResponse, CreateConversationRequest
from app.services import chat as chat_service

router = APIRouter()


@router.post(
    "/chat/conversations",
    response_model=ApiResponse[ConversationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="새 대화 시작",
    responses=error_responses(401, 404, 422),
)
def start_conversation(
    request: CreateConversationRequest | None = None,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[ConversationResponse]:
    """진행 중인 대화를 닫고 새 대화를 연다.

    `mealId` 가 없으면 서버가 가장 최근 확정 끼니로 정한다. 기준 끼니에 보여 줄 추천이
    있으면 추천 카드를 담은 AI 메시지 1개, 없으면 `messages: []` 다.

    **body 는 생략할 수 있다.** AI 탭에서 바로 "＋ 새 대화" 를 누르면 보낼 값이 없다.

    없는 식사 · 남의 식사 · 삭제된 식사는 전부 404 로 같게 응답한다 (소유권 누출 방지).
    존재하지 않는 사용자는 404 `USER_NOT_FOUND` 다.
    """
    try:
        return ok(
            chat_service.start_conversation(
                db,
                user_id=user_id,
                meal_id=None if request is None else request.meal_id,
            )
        )
    except chat_service.UserNotFoundError as exc:
        raise ApiError(ErrorCode.USER_NOT_FOUND, str(exc), 404) from None
    except chat_service.MealNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, str(exc), 404) from None


def _conversation_ok(view: ConversationResponse) -> ApiResponse[ConversationResponse]:
    """A-3 · A-4 공통 래핑. **마지막 메시지가 BLOCKED 면** `MEDICAL_QUESTION_DETECTED` 를
    싣는다 (명세: 가드레일 감지는 200 + `error.code`).

    마지막 메시지만 보는 이유: 폴링 대상은 방금 보낸 질문의 답이다. 지난 BLOCKED 까지
    보면 이후 조회마다 코드가 붙어 FE 가 매번 상담 안내를 띄운다.
    `feedbacks.py::get_meal_feedback` 과 같은 모양이다.
    """
    if view.messages and view.messages[-1].safety_status is SafetyStatus.BLOCKED:
        return ok_with_code(
            view,
            ErrorCode.MEDICAL_QUESTION_DETECTED,
            "의료 판단이 필요한 질문이라 답변 대신 상담을 안내해요.",
        )
    return ok(view)


# `/latest` 를 `/{conversation_id}` 보다 **먼저** 등록한다. 순서가 바뀌면 "latest" 가
# UUID 경로 파라미터에 잡혀 422 가 난다 (`test_latest_without_conversation_is_200_null`).
@router.get(
    "/chat/conversations/latest",
    response_model=ApiResponse[ConversationResponse | None],
    summary="진행 중인 대화 이어 보기",
    responses=error_responses(401, 422),
)
def get_latest_conversation(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[ConversationResponse | None]:
    """AI 탭에 들어올 때 이어 볼 열린 대화. 메시지 모양은 A-3 과 같다.

    **열린 대화가 없으면 200 `data: null` 이다** — 에러가 아니라 "아직 없음" 이고, FE 는
    이때 `POST /chat/conversations` 로 새 대화를 연다. 닫힌 대화는 돌려주지 않는다.
    마지막 답이 BLOCKED 면 A-3 처럼 `MEDICAL_QUESTION_DETECTED` 가 붙는다.
    """
    view = chat_service.get_latest_conversation(db, user_id=user_id)
    if view is None:
        return ok(None)
    return _conversation_ok(view)


@router.get(
    "/chat/conversations/{conversation_id}",
    response_model=ApiResponse[ConversationResponse],
    summary="메시지 목록 · 답변 폴링",
    responses=error_responses(401, 404, 422),
)
def get_conversation(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[ConversationResponse]:
    """대화의 메시지 전부를 오래된 순으로 준다.

    FE 는 A-2 의 `assistantMessageId` 가 `READY` · `FAILED` 가 될 때까지 이걸 폴링한다.
    마지막 답이 가드레일에 막혔으면(`safetyStatus: BLOCKED`) 200 에
    `MEDICAL_QUESTION_DETECTED` 가 붙는다.

    **닫힌 대화도 200 이다** — "＋ 새 대화" 뒤에도 폴링 중이던 답을 받을 수 있다.
    없는 대화 · 남의 대화는 404 로 같게 응답한다 (소유권 누출 방지).
    """
    try:
        view = chat_service.get_conversation(
            db, user_id=user_id, conversation_id=conversation_id
        )
    except chat_service.ConversationNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, str(exc), 404) from None
    return _conversation_ok(view)
