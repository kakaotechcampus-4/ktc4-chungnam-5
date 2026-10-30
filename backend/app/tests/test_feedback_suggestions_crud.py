"""A-5 가 쓰는 crud — `crud/feedback.py::is_showable` · `replace_suggestions`,
`crud/chat.py::get_card_for_meal`. HTTP 계약은 `test_feedback_suggestions_api.py` 가 본다."""

import uuid

import pytest
from sqlalchemy.orm import Session

from app.crud import chat as chat_crud
from app.crud import feedback as feedback_crud
from app.models.chat import ChatMessage
from app.models.enums import ChatMessageRole, ChatMessageStatus, MealStatus, SafetyStatus
from app.tests.factories import make_meal, make_meal_feedback, make_user

_TOFU = {"foodName": "두부 반 모", "advice": "단백질을 10g 더 채워요", "candidateFoodRefId": "KFD_TOFU"}


def _card(db: Session, *, user_id, meal_id):
    """열린 대화를 닫고 새 대화 · 메시지 · 카드 1장을 만든다(사용자당 열린 대화 1개 제약)."""
    chat_crud.close_open_conversation(db, user_id)
    conversation = chat_crud.create_conversation(db, user_id=user_id, meal_id=meal_id)
    message = chat_crud.add_message(
        db,
        conversation_id=conversation.id,
        role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.READY,
        text="다음 끼니로 이 음식은 어떤가요?",
        safety_status=SafetyStatus.SAFE,
    )
    return chat_crud.add_food_card(
        db, message_id=message.id, position=0,
        food_name="쌈채소 한 접시", advice="식이섬유를 채워요", food_ref_id="KFD_SALAD",
    )


# ── is_showable ─────────────────────────────────────────────


def test_safe_row_with_body_is_showable(db: Session) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    row = make_meal_feedback(db, user_id=user.id, meal_id=meal.id)

    assert feedback_crud.is_showable(row) is True


def test_missing_row_is_not_showable() -> None:
    assert feedback_crud.is_showable(None) is False


@pytest.mark.parametrize("body", [None, "", "   "], ids=["NULL", "빈 문자열", "공백"])
def test_row_without_body_is_not_showable(db: Session, body) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    row = make_meal_feedback(db, user_id=user.id, meal_id=meal.id, body=body)

    assert feedback_crud.is_showable(row) is False


@pytest.mark.parametrize("safety", [SafetyStatus.BLOCKED, SafetyStatus.REVIEW_REQUIRED])
def test_unsafe_row_is_not_showable(db: Session, safety) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    row = make_meal_feedback(db, user_id=user.id, meal_id=meal.id, safety_status=safety)

    assert feedback_crud.is_showable(row) is False


# ── replace_suggestions ─────────────────────────────────────


def test_replace_suggestions_overwrites_only_suggestions(db: Session) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    row = make_meal_feedback(db, user_id=user.id, meal_id=meal.id, suggestions=[_TOFU, _TOFU])
    before = (row.body, row.reasoning, row.safety_status, row.model_version)
    new = [{"foodName": "쌈채소 한 접시", "advice": "식이섬유를 채워요", "candidateFoodRefId": None}]

    feedback_crud.replace_suggestions(db, row, new)
    db.expire(row)  # DB 에 실제로 쓰였는지 다시 읽어 본다

    assert row.suggestions == new
    assert (row.body, row.reasoning, row.safety_status, row.model_version) == before


# ── get_card_for_meal ───────────────────────────────────────


def test_finds_own_card_of_the_same_meal_with_message_time(db: Session) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    card = _card(db, user_id=user.id, meal_id=meal.id)

    found = chat_crud.get_card_for_meal(db, card_id=card.id, user_id=user.id, meal_id=meal.id)

    assert found is not None
    found_card, created_at = found
    assert found_card.id == card.id
    assert created_at == db.get(ChatMessage, card.message_id).created_at


def test_finds_card_of_a_closed_conversation(db: Session) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    card = _card(db, user_id=user.id, meal_id=meal.id)
    chat_crud.close_open_conversation(db, user.id)

    assert chat_crud.get_card_for_meal(db, card_id=card.id, user_id=user.id, meal_id=meal.id) is not None


def test_card_of_another_user_is_not_found(db: Session) -> None:
    owner = make_user(db)
    stranger = make_user(db, nickname="남")
    meal = make_meal(db, user_id=owner.id, status=MealStatus.EVALUATED)
    card = _card(db, user_id=owner.id, meal_id=meal.id)

    assert chat_crud.get_card_for_meal(db, card_id=card.id, user_id=stranger.id, meal_id=meal.id) is None


def test_card_of_another_meal_is_not_found(db: Session) -> None:
    user = make_user(db)
    lunch = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    dinner = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    card = _card(db, user_id=user.id, meal_id=lunch.id)

    assert chat_crud.get_card_for_meal(db, card_id=card.id, user_id=user.id, meal_id=dinner.id) is None


def test_card_of_conversation_without_meal_is_not_found(db: Session) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    card = _card(db, user_id=user.id, meal_id=None)

    assert chat_crud.get_card_for_meal(db, card_id=card.id, user_id=user.id, meal_id=meal.id) is None


def test_unknown_card_is_not_found(db: Session) -> None:
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)

    assert chat_crud.get_card_for_meal(db, card_id=uuid.uuid4(), user_id=user.id, meal_id=meal.id) is None
