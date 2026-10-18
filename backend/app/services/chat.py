"""챗봇(AI 코치) 대화.

`crud/` 를 조합하고 트랜잭션 경계를 정한다 (절대 규칙 5). 다른 `services/` 를
참조하지 않는다 — 끼니 제안 해석은 `crud/feedback.py::parse_suggestions` 를 같이 쓴다.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.orm import Session

from app.crud import chat as chat_crud
from app.crud import feedback as feedback_crud
from app.crud import meal as meal_crud
from app.crud.feedback import StoredSuggestion
from app.infra.queue import enqueue
from app.models.chat import ChatFoodCard, ChatMessage
from app.models.enums import (
    ChatMessageRole,
    ChatMessageStatus,
    MealStatus,
    SafetyStatus,
)
from app.schemas.chat import (
    CHAT_REPLY_POLL_INTERVAL_MS,
    CHAT_REPLY_TIMEOUT_MS,
    ChatMessageResponse,
    ConversationResponse,
    FoodCardEffect,
    FoodCardResponse,
    SendMessageResponse,
)

INTRO_TEXT: Final = "다음 끼니로 이 음식은 어떤가요?"
"""새 대화의 첫 AI 메시지. **AI 가 쓴 문장이 아니라 BE 고정 문구**다 — A-1 은 201 동기
응답이라 AI 를 부를 수 없다. 끼니 종류별 문구는 두지 않기로 했다(설계 결정)."""

class MealNotFoundError(LookupError):
    """없는 식사 · 남의 식사 · 삭제된 식사. 셋 다 404 로 같게 응답한다."""


class UserNotFoundError(LookupError):
    """`users` 행이 없다. 인증 헤더는 형식만 확인하므로 존재하지 않는 id 가 올 수 있다."""


class ConversationNotFoundError(LookupError):
    """없는 대화 · 남의 대화 · 닫힌 대화. 셋 다 404 로 같게 응답한다."""


class ReplyInProgressError(RuntimeError):
    """직전 답변이 아직 생성 중이다 (연속 전송 방지, 409)."""


def showable_suggestions(db: Session, meal_id: uuid.UUID) -> list[StoredSuggestion]:
    """기준 끼니의 "현재 추천" 중 지금 보여 줘도 되는 것. 없으면 `[]`.

    끼니 피드백 화면(`services/feedback.py::get_feedback`)이 `READY` 로 내용을 싣는 조건과
    같다 — SAFE 이고 본문이 있을 때만. 거기서 안 보이는 추천이 챗봇에서 보이면 안 된다
    (BLOCKED · 검수 전 문장은 절대 규칙 1).

    `chat.reply` 워커가 기준 끼니 컨텍스트를 만들 때도 이 함수를 쓴다 — 커밋하지 않는 읽기라
    워커가 불러도 된다(`worker/dispatch.py::get_job` 독스트링).
    """
    row = feedback_crud.get_by_meal(db, meal_id)
    if row is None or row.safety_status is not SafetyStatus.SAFE:
        return []
    if not (row.body or "").strip():
        return []
    return feedback_crud.parse_suggestions(row.suggestions)


def _card_responses(db: Session, cards: list[ChatFoodCard]) -> list[FoodCardResponse]:
    """카드에 성분을 채운다. `food_refs` 를 **한 번** 읽는다. 못 찾으면 `effects: []`."""
    by_ref = feedback_crud.nutrients_by_food_ref(
        db, [card.food_ref_id for card in cards if card.food_ref_id is not None]
    )
    return [
        FoodCardResponse(
            food_card_id=card.id,
            food_name=card.food_name,
            effects=[
                FoodCardEffect(code=n.code, amount=float(n.amount), unit=n.unit)
                for n in (
                    by_ref[card.food_ref_id].nutrients if card.food_ref_id in by_ref else []
                )
            ],
        )
        for card in cards
    ]


def _message_response(
    message: ChatMessage, cards: list[FoodCardResponse]
) -> ChatMessageResponse:
    return ChatMessageResponse(
        message_id=message.id,
        role=message.role,
        status=message.status,
        text=message.text,
        food_cards=cards,
        safety_status=message.safety_status,
        created_at=message.created_at,
    )


def start_conversation(
    db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID | None
) -> ConversationResponse:
    """열린 대화를 닫고 새 대화를 연다 (A-1). 한 트랜잭션이다.

    기준 끼니:
        `meal_id` 지정   그 끼니. 없음·남의 것·삭제됨이면 `MealNotFoundError`.
                         확정 전이어도 기준은 된다 — 상태는 404 사유가 아니다.
        미지정           가장 최근 확정 끼니. 없으면 `None`.

    첫 메시지는 기준 끼니가 **확정돼 있고** 보여 줄 추천이 있을 때만 하나 만든다.
    아니면 `messages: []` 다 — "준비 중" 같은 문구를 지어내면 나중에 갱신되지 않고 낡는다.
    확정 전 끼니를 거르는 이유는 `services/feedback.py::get_feedback` 과 같다 — 음식을
    고치는 중이면 저장된 제안은 고치기 전 끼니를 보고 쓴 것이다.

    없는 사용자면 다른 작업 전에 `UserNotFoundError`.
    """
    if not chat_crud.lock_user(db, user_id):
        raise UserNotFoundError(f"user {user_id} 를 찾을 수 없습니다.")

    if meal_id is not None:
        meal = meal_crud.get_owned_meal(db, user_id=user_id, meal_id=meal_id)
        if meal is None:
            raise MealNotFoundError(f"meal {meal_id} 를 찾을 수 없습니다.")
        base_meal_id: uuid.UUID | None = meal.id
        evaluated = meal.status is MealStatus.EVALUATED
    else:
        base_meal_id = chat_crud.latest_evaluated_meal_id(db, user_id)
        evaluated = base_meal_id is not None

    suggestions = (
        showable_suggestions(db, base_meal_id)
        if evaluated and base_meal_id is not None
        else []
    )

    chat_crud.close_open_conversation(db, user_id)
    conversation = chat_crud.create_conversation(db, user_id=user_id, meal_id=base_meal_id)

    intro: tuple[ChatMessage, list[ChatFoodCard]] | None = None
    if suggestions:
        message = chat_crud.add_message(
            db,
            conversation_id=conversation.id,
            role=ChatMessageRole.ASSISTANT,
            status=ChatMessageStatus.READY,
            text=INTRO_TEXT,
            # 고정 문구 + 이미 SAFE 판정을 받은 제안의 복사본이라 새로 검사할 문장이 없다.
            safety_status=SafetyStatus.SAFE,
        )
        cards = [
            chat_crud.add_food_card(
                db,
                message_id=message.id,
                position=position,
                food_name=suggestion.food_name,
                advice=suggestion.advice,
                food_ref_id=suggestion.ref_id,
            )
            for position, suggestion in enumerate(suggestions)
        ]
        intro = (message, cards)

    db.commit()

    messages = [] if intro is None else [_message_response(intro[0], _card_responses(db, intro[1]))]
    return ConversationResponse(
        conversation_id=conversation.id,
        meal_id=base_meal_id,
        messages=messages,
    )


def send_message(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID, text: str
) -> SendMessageResponse:
    """사용자 메시지를 접수하고 답변 생성을 큐에 넣는다 (A-2). 한 트랜잭션이다.

    직전 답변이 생성 중이면 `ReplyInProgressError`. 단 만든 지
    `chat_crud.CHAT_REPLY_STALE_SEC` 가 지났으면 워커가 죽은 것으로 보고 FAILED 로
    닫은 뒤 받는다 — 아니면 그 대화는 영영 막힌다.

    컨텍스트(투약 · 오늘 끼니 · 기준 끼니 추천 등)는 여기서 모으지 않는다. 워커가
    AI 를 부르기 직전에 모은다 — 큐 payload 에 건강정보를 남기지 않고(규칙 6), 그 사이
    확정된 끼니도 반영된다.
    """
    conversation = chat_crud.lock_open_conversation(
        db, user_id=user_id, conversation_id=conversation_id
    )
    if conversation is None:
        raise ConversationNotFoundError(f"conversation {conversation_id} 를 찾을 수 없습니다.")

    pending = chat_crud.find_generating_reply(db, conversation.id)
    if pending is not None:
        reply, stale = pending
        if not stale:
            raise ReplyInProgressError("이전 답변을 아직 만들고 있습니다.")
        chat_crud.finish_message(db, reply, status=ChatMessageStatus.FAILED)

    question = chat_crud.add_message(
        db,
        conversation_id=conversation.id,
        role=ChatMessageRole.USER,
        status=ChatMessageStatus.READY,
        text=text,
        safety_status=None,
    )
    reply = chat_crud.add_message(
        db,
        conversation_id=conversation.id,
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.GENERATING,
        text=None,
        safety_status=None,
    )
    enqueue(db, "chat.reply", {"messageId": str(reply.id)})

    db.commit()

    return SendMessageResponse(
        user_message=_message_response(question, []),
        assistant_message_id=reply.id,
        poll_interval_ms=CHAT_REPLY_POLL_INTERVAL_MS,
        timeout_ms=CHAT_REPLY_TIMEOUT_MS,
    )
