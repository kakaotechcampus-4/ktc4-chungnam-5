"""`services/chat.py::get_conversation` — A-3 대화 조회의 동작.

HTTP 모양(200 · 404 단일화 · 가드레일 코드)은 `test_chat_conversation_read_api.py` 가 본다.
메시지는 `crud/chat.py` 로 직접 넣는다 — 프로덕션에서는 A-1 · A-2 · `chat.reply` 워커가 넣는다.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from app.crud import chat as chat_crud
from app.models.enums import (
    ChatMessageRole,
    ChatMessageStatus,
    MealStatus,
    NutrientCode,
    SafetyStatus,
)
from app.services import chat as chat_service
from app.tests.factories import make_food_ref, make_meal, make_user


def _conversation(db: Session, user_id, meal_id=None):
    return chat_crud.create_conversation(db, user_id=user_id, meal_id=meal_id)


def _message(db: Session, conversation_id, role=ChatMessageRole.ASSISTANT, **kwargs):
    defaults = {
        "status": ChatMessageStatus.READY,
        "text": "안녕하세요",
        "safety_status": SafetyStatus.SAFE if role is ChatMessageRole.ASSISTANT else None,
    }
    return chat_crud.add_message(
        db, conversation_id=conversation_id, role=role, **{**defaults, **kwargs}
    )


def _card(db: Session, message_id, position, food_name, food_ref_id=None):
    return chat_crud.add_food_card(
        db,
        message_id=message_id,
        position=position,
        food_name=food_name,
        advice="조언",
        food_ref_id=food_ref_id,
    )


def test_returns_messages_oldest_first(db: Session):
    user = make_user(db)
    conversation = _conversation(db, user.id)
    first = _message(db, conversation.id, text="다음 끼니로 이 음식은 어떤가요?")
    question = _message(db, conversation.id, role=ChatMessageRole.USER, text="다른 거 추천해줘")
    reply = _message(db, conversation.id, status=ChatMessageStatus.GENERATING, text=None, safety_status=None)

    result = chat_service.get_conversation(db, user_id=user.id, conversation_id=conversation.id)

    assert result.conversation_id == conversation.id
    assert result.meal_id is None
    assert [m.message_id for m in result.messages] == [first.id, question.id, reply.id]
    assert result.messages[1].role is ChatMessageRole.USER
    assert result.messages[1].safety_status is None
    assert result.messages[2].status is ChatMessageStatus.GENERATING
    assert result.messages[2].text is None


def test_cards_are_attached_to_their_message_in_position_order(db: Session):
    user = make_user(db)
    make_food_ref(
        db, food_ref_id="KFD_TOFU", name="두부", protein_g=8, fiber_g=1,
        calories=None, fat_g=None, carbohydrate_g=None, sodium_mg=None,
    )
    conversation = _conversation(db, user.id)
    intro = _message(db, conversation.id)
    # 넣는 순서를 뒤집어 position 으로 정렬하는지 본다.
    _card(db, intro.id, 1, "쌈채소 한 접시")
    _card(db, intro.id, 0, "두부 반 모", food_ref_id="KFD_TOFU")
    _message(db, conversation.id, role=ChatMessageRole.USER, text="고마워")
    later = _message(db, conversation.id)
    _card(db, later.id, 0, "현미밥", food_ref_id="KFD_MISSING")

    result = chat_service.get_conversation(db, user_id=user.id, conversation_id=conversation.id)

    intro_view, question_view, later_view = result.messages
    assert [c.food_name for c in intro_view.food_cards] == ["두부 반 모", "쌈채소 한 접시"]
    assert question_view.food_cards == []
    assert [c.food_name for c in later_view.food_cards] == ["현미밥"]

    tofu, salad = intro_view.food_cards
    assert {(e.code, e.amount, e.unit) for e in tofu.effects} == {
        (NutrientCode.PROTEIN, 8.0, "g"),
        (NutrientCode.FIBER, 1.0, "g"),
    }
    assert salad.effects == []  # food_ref_id 가 없다
    assert later_view.food_cards[0].effects == []  # food_refs 에 없는 id


def test_closed_conversation_is_still_readable(db: Session):
    """워커는 닫힌 대화의 마지막 질문에도 답을 쓴다 — 폴링 중이던 FE 가 그 답을 받아야 한다."""
    user = make_user(db)
    old = chat_service.start_conversation(db, user_id=user.id, meal_id=None)
    chat_service.start_conversation(db, user_id=user.id, meal_id=None)  # old 를 닫는다

    result = chat_service.get_conversation(db, user_id=user.id, conversation_id=old.conversation_id)

    assert result.conversation_id == old.conversation_id


def test_others_conversation_is_not_found(db: Session):
    owner = make_user(db)
    stranger = make_user(db, nickname="남")
    conversation = _conversation(db, owner.id)

    with pytest.raises(chat_service.ConversationNotFoundError):
        chat_service.get_conversation(db, user_id=stranger.id, conversation_id=conversation.id)


def test_missing_conversation_is_not_found(db: Session):
    user = make_user(db)

    with pytest.raises(chat_service.ConversationNotFoundError):
        chat_service.get_conversation(db, user_id=user.id, conversation_id=uuid.uuid4())


def test_live_base_meal_id_is_returned(db: Session):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    conversation = _conversation(db, user.id, meal_id=meal.id)

    result = chat_service.get_conversation(db, user_id=user.id, conversation_id=conversation.id)

    assert result.meal_id == meal.id


def test_soft_deleted_base_meal_is_null(db: Session):
    """끼니는 soft delete 라 대화가 삭제된 끼니를 가리킬 수 있다. 사용자에게 없는 끼니를
    내보내면 FE 가 그 id 로 A-5 를 불러 404 를 받는다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    conversation = _conversation(db, user.id, meal_id=meal.id)
    meal.deleted_at = datetime.now(UTC)
    db.flush()

    result = chat_service.get_conversation(db, user_id=user.id, conversation_id=conversation.id)

    assert result.meal_id is None


# --- A-4 `get_latest_conversation` ---


def test_latest_is_none_without_conversation(db: Session):
    user = make_user(db)

    assert chat_service.get_latest_conversation(db, user_id=user.id) is None


def test_latest_is_the_open_conversation(db: Session):
    user = make_user(db)
    chat_service.start_conversation(db, user_id=user.id, meal_id=None)  # 곧 닫힌다
    current = chat_service.start_conversation(db, user_id=user.id, meal_id=None)
    _message(db, current.conversation_id)

    result = chat_service.get_latest_conversation(db, user_id=user.id)

    assert result is not None
    assert result.conversation_id == current.conversation_id
    assert len(result.messages) == 1


def test_latest_ignores_closed_and_others_conversations(db: Session):
    """닫힌 대화만 남았으면 이어 볼 대화가 없다. 남의 열린 대화는 당연히 안 보인다."""
    user = make_user(db)
    stranger = make_user(db, nickname="남")
    _conversation(db, user.id)
    chat_crud.close_open_conversation(db, user.id)
    _conversation(db, stranger.id)

    assert chat_service.get_latest_conversation(db, user_id=user.id) is None
