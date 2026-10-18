"""챗봇 대화 · 메시지 · 음식 카드 접근.

`chat_conversations` 는 부분 UNIQUE `(user_id) WHERE closed_at IS NULL` 이라 새 대화를
열기 전에 **먼저 닫아야** 한다 (`close_open_conversation`). add/flush 까지만 하고
커밋하지 않는다 (절대 규칙 5).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.chat import ChatConversation, ChatFoodCard, ChatMessage
from app.models.enums import ChatMessageRole, ChatMessageStatus, MealStatus, SafetyStatus
from app.models.meal import Meal
from app.models.user import User


def lock_user(db: Session, user_id: uuid.UUID) -> bool:
    """같은 사용자의 "새 대화 시작" 을 줄 세운다. 트랜잭션이 끝날 때 풀린다.
    사용자 행이 있으면 True, 없으면 False (존재 확인이 잠금 쿼리에 얹힌다).

    잠그지 않으면 더블탭 두 요청이 둘 다 "열린 대화 없음" 을 보고 INSERT 해 둘째가
    부분 UNIQUE 에 걸려 500 이 난다. 잠그면 둘째가 첫째의 커밋을 기다렸다가 그 대화를
    닫고 연다 — 둘 다 201, 마지막 것만 열린다.

    **`users` 를 읽지만 여기 둔다.** 잠금의 목적이 챗봇 불변식이라서다
    (`crud/feedback.py::nutrients_by_food_ref` 가 `food_refs` 를 읽는 것과 같은 판단).

    **`FOR UPDATE` 가 아니라 `FOR NO KEY UPDATE`(`key_share=True`) 다.** `feedback.meal` 워커는
    `meals` 를 잠근 뒤 `meal_feedbacks` 를 upsert 하는데, 그 `user_id` FK 검사가 `users` 에
    `FOR KEY SHARE` 를 건다. 여기서 `users` 를 `FOR UPDATE` 로 쥔 채 `chat_conversations`
    (`meal_id` FK → `meals` KEY SHARE)를 넣으면 두 쪽의 잠금 순서가 맞물려 교착한다.
    `NO KEY UPDATE` 는 KEY SHARE 와 충돌하지 않으면서 자기끼리는 충돌하므로, 같은 사용자의
    A-1 끼리만 줄 세우고 그 사용자의 다른 쓰기(FK 검사)는 막지 않는다.
    """
    row = db.execute(
        select(User.id).where(User.id == user_id).with_for_update(key_share=True)
    ).scalar_one_or_none()
    return row is not None


def latest_evaluated_meal_id(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    """`mealId` 없이 새 대화를 열 때의 기준 끼니 — 가장 최근에 먹은 **확정된** 살아있는 끼니.

    확정 전 끼니는 추천이 없거나 낡았고, 삭제된 끼니는 사용자에게 없는 끼니다.
    `ix_meals_user_id_eaten_at`(부분 인덱스, `deleted_at IS NULL`)를 탄다.
    """
    stmt = (
        select(Meal.id)
        .where(
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.status == MealStatus.EVALUATED,
        )
        .order_by(Meal.eaten_at.desc(), Meal.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def close_open_conversation(db: Session, user_id: uuid.UUID) -> None:
    """열린 대화를 닫는다. 없으면 0 행 UPDATE 다."""
    db.execute(
        update(ChatConversation)
        .where(ChatConversation.user_id == user_id, ChatConversation.closed_at.is_(None))
        .values(closed_at=func.now())
    )


def create_conversation(
    db: Session, *, user_id: uuid.UUID, meal_id: uuid.UUID | None
) -> ChatConversation:
    conversation = ChatConversation(user_id=user_id, meal_id=meal_id)
    db.add(conversation)
    db.flush()
    return conversation


def add_message(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    role: ChatMessageRole,
    status: ChatMessageStatus,
    text: str | None,
    safety_status: SafetyStatus | None,
) -> ChatMessage:
    """메시지 하나를 넣는다. 메시지마다 flush 한다 — `created_at` 이 INSERT 시각
    (`clock_timestamp()`)이라 넣은 순서가 곧 정렬 순서가 되게 하려면 한 번에 몰아 넣지 않는다."""
    message = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        status=status,
        text=text,
        safety_status=safety_status,
    )
    db.add(message)
    db.flush()
    return message


def add_food_card(
    db: Session,
    *,
    message_id: uuid.UUID,
    position: int,
    food_name: str,
    advice: str,
    food_ref_id: str | None,
) -> ChatFoodCard:
    card = ChatFoodCard(
        message_id=message_id,
        position=position,
        food_name=food_name,
        advice=advice,
        food_ref_id=food_ref_id,
    )
    db.add(card)
    db.flush()
    return card


def get_card_for_meal(
    db: Session, *, card_id: uuid.UUID, user_id: uuid.UUID, meal_id: uuid.UUID
) -> tuple[ChatFoodCard, datetime] | None:
    """A-5 "이걸로 바꾸기" 가 쓸 카드와, 그 카드가 만들어진 시각(메시지 `created_at`).

    카드 → 메시지 → 대화를 한 번에 JOIN 해 **대화의 주인과 기준 끼니가 둘 다 맞을 때만**
    돌려준다. 없는 카드 · 남의 카드 · 다른 끼니 대화의 카드를 구분하지 않고 모두 `None` 이다
    — `crud/meal.py::get_owned_meal` 처럼 "남의 카드 id 는 존재한다" 는 정보를 흘리지 않는다.

    닫힌 대화(`closed_at`)의 카드도 돌려준다. 열린 대화도 사용자가 닫기 전까지 계속 열려
    있어 "닫힘" 은 카드가 낡았는지를 말해 주지 않는다 — 낡음은 호출부가 메시지 시각과
    끼니의 마지막 확정 시각으로 판정한다.
    """
    stmt = (
        select(ChatFoodCard, ChatMessage.created_at)
        .join(ChatMessage, ChatMessage.id == ChatFoodCard.message_id)
        .join(ChatConversation, ChatConversation.id == ChatMessage.conversation_id)
        .where(
            ChatFoodCard.id == card_id,
            ChatConversation.user_id == user_id,
            ChatConversation.meal_id == meal_id,
        )
    )
    row = db.execute(stmt).one_or_none()
    return None if row is None else (row[0], row[1])
