"""챗봇(AI 코치) API (`contracts/ChatbotAPI.md`).

이 층이 하는 일은 넷뿐이다 — 인증 의존성으로 유저 확인, 요청 검증, 서비스 호출,
도메인 예외를 HTTP 로 번역.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user_id
from app.core.errors import ApiError, ErrorCode
from app.core.response import ApiResponse, error_responses, ok
from app.db.session import get_db
from app.schemas.chat import (
    ConversationResponse,
    CreateConversationRequest,
    SendMessageRequest,
    SendMessageResponse,
)
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


@router.post(
    "/chat/conversations/{conversation_id}/messages",
    response_model=ApiResponse[SendMessageResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="사용자 메시지 보내기",
    responses=error_responses(401, 404, 409, 422),
)
def send_message(
    conversation_id: uuid.UUID,
    request: SendMessageRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ApiResponse[SendMessageResponse]:
    """사용자 메시지를 접수하고 AI 답변 생성을 시작한다.

    답변은 비동기다 — `assistantMessageId` 가 `READY` · `FAILED` 가 될 때까지
    `pollIntervalMs` 간격으로, 최대 `timeoutMs` 동안 대화를 다시 조회한다.

    없는 대화 · 남의 대화 · 닫힌 대화는 전부 404 로 같게 응답한다 (소유권 누출 방지).
    이전 답변이 아직 생성 중이면 409 `CONFLICT` 다.
    """
    try:
        return ok(
            chat_service.send_message(
                db, user_id=user_id, conversation_id=conversation_id, text=request.text
            )
        )
    except chat_service.ConversationNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, str(exc), 404) from None
    except chat_service.ReplyInProgressError as exc:
        raise ApiError(ErrorCode.CONFLICT, str(exc), 409) from None
