"""`services/chat.py::start_conversation` — A-1 새 대화 시작의 동작.

HTTP 모양(camelCase · 201 · 에러 래퍼)은 `test_chat_conversations_api.py` 가 본다.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.chat import ChatConversation, ChatFoodCard
from app.models.enums import (
    ChatMessageRole,
    ChatMessageStatus,
    MealStatus,
    NutrientCode,
    SafetyStatus,
)
from app.services import chat as chat_service
from app.tests.factories import EATEN_AT, make_food_ref, make_meal, make_meal_feedback, make_user

_TOFU = {"foodName": "두부 반 모", "advice": "단백질을 10g 더 채워요", "candidateFoodRefId": "KFD_TOFU"}
_SALAD = {"foodName": "쌈채소 한 접시", "advice": "식이섬유를 채워요"}


def _evaluated_meal_with_suggestions(db: Session, user_id, suggestions=None, **meal_kwargs):
    meal = make_meal(db, user_id=user_id, status=MealStatus.EVALUATED, **meal_kwargs)
    make_meal_feedback(
        db, user_id=user_id, meal_id=meal.id, suggestions=suggestions if suggestions is not None else [_TOFU, _SALAD]
    )
    return meal


def _open_conversations(db: Session, user_id) -> int:
    return db.scalar(
        select(func.count()).select_from(ChatConversation).where(
            ChatConversation.user_id == user_id, ChatConversation.closed_at.is_(None)
        )
    )


def test_explicit_meal_gets_intro_message_with_cards(db: Session):
    user = make_user(db)
    make_food_ref(db, food_ref_id="KFD_TOFU", name="두부", protein_g=8, fiber_g=1)
    meal = _evaluated_meal_with_suggestions(db, user.id)

    result = chat_service.start_conversation(db, user_id=user.id, meal_id=meal.id)

    assert result.meal_id == meal.id
    [message] = result.messages
    assert message.role is ChatMessageRole.ASSISTANT
    assert message.status is ChatMessageStatus.READY
    assert message.safety_status is SafetyStatus.SAFE
    assert message.text == chat_service.INTRO_TEXT == "다음 끼니로 이 음식은 어떤가요?"
    assert [card.food_name for card in message.food_cards] == ["두부 반 모", "쌈채소 한 접시"]

    tofu, salad = message.food_cards
    # make_food_ref 의 serving_size 기본값이 100 이라 100g 환산값이 그대로다.
    assert {(e.code, e.amount, e.unit) for e in tofu.effects} == {
        (NutrientCode.PROTEIN, 8.0, "g"),
        (NutrientCode.FIBER, 1.0, "g"),
    }
    assert salad.effects == []  # candidateFoodRefId 가 없다


def test_cards_are_stored_with_advice_and_ref(db: Session):
    """A-5 가 foodCardId 로 찾아 suggestions 로 되돌리므로 advice · ref 까지 저장돼야 한다."""
    user = make_user(db)
    meal = _evaluated_meal_with_suggestions(db, user.id)

    result = chat_service.start_conversation(db, user_id=user.id, meal_id=meal.id)

    card_id = result.messages[0].food_cards[0].food_card_id
    stored = db.get(ChatFoodCard, card_id)
    assert (stored.position, stored.food_name, stored.advice, stored.food_ref_id) == (
        0, "두부 반 모", "단백질을 10g 더 채워요", "KFD_TOFU",
    )


def test_ref_missing_from_food_refs_gives_empty_effects(db: Session):
    user = make_user(db)
    meal = _evaluated_meal_with_suggestions(db, user.id, suggestions=[_TOFU])  # KFD_TOFU 를 안 만든다

    result = chat_service.start_conversation(db, user_id=user.id, meal_id=meal.id)

    assert result.messages[0].food_cards[0].effects == []


def test_without_meal_id_picks_latest_evaluated_meal(db: Session):
    """더 최근이어도 확정 전 · 삭제된 끼니는 기준이 아니다."""
    user = make_user(db)
    older = _evaluated_meal_with_suggestions(db, user.id, eaten_at=EATEN_AT)
    latest = _evaluated_meal_with_suggestions(db, user.id, eaten_at=EATEN_AT + timedelta(hours=5))
    make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED, eaten_at=EATEN_AT + timedelta(hours=8))
    deleted = _evaluated_meal_with_suggestions(db, user.id, eaten_at=EATEN_AT + timedelta(hours=9))
    deleted.deleted_at = EATEN_AT + timedelta(hours=10)
    db.flush()

    result = chat_service.start_conversation(db, user_id=user.id, meal_id=None)

    assert result.meal_id == latest.id != older.id
    assert len(result.messages) == 1


def test_no_evaluated_meal_gives_null_meal_and_no_messages(db: Session):
    user = make_user(db)
    make_meal(db, user_id=user.id, status=MealStatus.REVIEW_REQUIRED)

    result = chat_service.start_conversation(db, user_id=user.id, meal_id=None)

    assert result.meal_id is None
    assert result.messages == []
    assert _open_conversations(db, user.id) == 1


@pytest.mark.parametrize(
    "feedback_kwargs",
    [
        None,  # 피드백 행이 아직 없다
        {"safety_status": SafetyStatus.BLOCKED},
        {"safety_status": SafetyStatus.REVIEW_REQUIRED},
        {"body": None},  # 재확정이 비운 행
        {"suggestions": []},
        {"suggestions": ["깨진 원소"]},
    ],
    ids=["no-row", "blocked", "review-required", "invalidated", "empty", "all-broken"],
)
def test_meal_without_showable_suggestions_keeps_meal_but_no_messages(db: Session, feedback_kwargs):
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.EVALUATED)
    if feedback_kwargs is not None:
        make_meal_feedback(db, user_id=user.id, meal_id=meal.id, **{"suggestions": [_TOFU], **feedback_kwargs})

    result = chat_service.start_conversation(db, user_id=user.id, meal_id=meal.id)

    assert result.meal_id == meal.id
    assert result.messages == []


def test_explicit_meal_not_yet_evaluated_keeps_meal_but_no_messages(db: Session):
    """확정 전 끼니도 404 가 아니다 — 상태는 404 사유가 아니다. 다만 저장된 제안은 낡았을 수 있어 안 보인다."""
    user = make_user(db)
    meal = make_meal(db, user_id=user.id, status=MealStatus.ANALYZING)
    make_meal_feedback(db, user_id=user.id, meal_id=meal.id, suggestions=[_TOFU])

    result = chat_service.start_conversation(db, user_id=user.id, meal_id=meal.id)

    assert result.meal_id == meal.id
    assert result.messages == []


@pytest.mark.parametrize("case", ["missing", "others", "deleted"])
def test_unusable_meal_id_raises_and_creates_nothing(db: Session, case):
    user = make_user(db)
    if case == "missing":
        meal_id = uuid.uuid4()
    elif case == "others":
        stranger = make_user(db, nickname="남")
        meal_id = _evaluated_meal_with_suggestions(db, stranger.id).id
    else:
        meal = _evaluated_meal_with_suggestions(db, user.id)
        meal.deleted_at = EATEN_AT
        db.flush()
        meal_id = meal.id

    with pytest.raises(chat_service.MealNotFoundError):
        chat_service.start_conversation(db, user_id=user.id, meal_id=meal_id)

    assert db.scalar(select(func.count()).select_from(ChatConversation)) == 0


def test_starting_again_closes_previous_conversation(db: Session):
    user = make_user(db)
    first = chat_service.start_conversation(db, user_id=user.id, meal_id=None)

    second = chat_service.start_conversation(db, user_id=user.id, meal_id=None)

    assert second.conversation_id != first.conversation_id
    assert db.get(ChatConversation, first.conversation_id).closed_at is not None
    assert db.get(ChatConversation, second.conversation_id).closed_at is None
    assert _open_conversations(db, user.id) == 1


def test_other_users_open_conversation_is_untouched(db: Session):
    user = make_user(db)
    stranger = make_user(db, nickname="남")
    theirs = chat_service.start_conversation(db, user_id=stranger.id, meal_id=None)

    chat_service.start_conversation(db, user_id=user.id, meal_id=None)

    assert db.get(ChatConversation, theirs.conversation_id).closed_at is None


def test_unknown_user_raises_and_creates_nothing(db: Session):
    """`users` 행이 없으면 FK 위반(500) 이 아니라 도메인 예외로 끝나야 한다."""
    ghost = uuid.uuid4()

    with pytest.raises(chat_service.UserNotFoundError):
        chat_service.start_conversation(db, user_id=ghost, meal_id=None)

    assert db.scalar(select(func.count()).select_from(ChatConversation)) == 0
