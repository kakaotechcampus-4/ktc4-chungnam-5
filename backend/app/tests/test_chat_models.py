"""챗봇 테이블의 DB 불변식.

서비스가 지키는 규칙이 아니라 **DB 가 막는 것**만 본다 — 서비스에 버그가 있어도
깨지면 안 되는 것들이다.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.chat import ChatConversation, ChatFoodCard, ChatMessage
from app.models.enums import ChatMessageRole, ChatMessageStatus, SafetyStatus
from app.tests.factories import EATEN_AT, make_user


def test_one_open_conversation_per_user(db: Session):
    """열린 대화가 둘이면 "진행 중인 대화" 가 모호해진다 (A-4)."""
    user = make_user(db)
    db.add(ChatConversation(user_id=user.id))
    db.flush()

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(ChatConversation(user_id=user.id))
            db.flush()


def test_closed_conversations_do_not_count(db: Session):
    user = make_user(db)
    db.add(ChatConversation(user_id=user.id, closed_at=EATEN_AT))
    db.flush()

    db.add(ChatConversation(user_id=user.id))
    db.flush()  # 터지지 않으면 통과


def test_messages_in_one_transaction_keep_insert_order(db: Session):
    """A-2 는 USER 메시지와 ASSISTANT 자리표시를 한 트랜잭션에 넣는다.

    `now()` 면 둘이 같은 시각이 되어 "오래된 순" 이 정해지지 않는다 —
    `clock_timestamp()` 라 뒤에 넣은 것이 엄격히 늦다.
    """
    user = make_user(db)
    conversation = ChatConversation(user_id=user.id)
    db.add(conversation)
    db.flush()

    first = ChatMessage(
        conversation_id=conversation.id,
        role=ChatMessageRole.USER,
        status=ChatMessageStatus.READY,
        text="안녕",
    )
    db.add(first)
    db.flush()
    second = ChatMessage(
        conversation_id=conversation.id,
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.GENERATING,
    )
    db.add(second)
    db.flush()

    assert first.created_at < second.created_at


def test_food_card_ref_without_food_refs_row_is_allowed(db: Session):
    """`food_ref_id` 에 FK 가 없다 — AI 가 준 id 가 `food_refs` 에 없어도 카드는 남는다."""
    user = make_user(db)
    conversation = ChatConversation(user_id=user.id)
    db.add(conversation)
    db.flush()
    message = ChatMessage(
        conversation_id=conversation.id,
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.READY,
        text="다음 끼니로 이 음식은 어떤가요?",
        safety_status=SafetyStatus.SAFE,
    )
    db.add(message)
    db.flush()

    db.add(
        ChatFoodCard(
            message_id=message.id,
            position=0,
            food_name="두부 반 모",
            advice="단백질을 채워요",
            food_ref_id="KFD_DOES_NOT_EXIST",
        )
    )
    db.flush()  # 터지지 않으면 통과
