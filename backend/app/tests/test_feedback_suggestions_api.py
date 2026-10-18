"""PUT /meals/{mealId}/feedback/suggestions (A-5) 의 HTTP 계약.

챗봇 카드 한 장으로 그 끼니의 다음 끼니 추천을 **전체 교체**한다. 규칙은
`docs/superpowers/specs/2026-10-18-meal-feedback-suggestion-replace-design.md` 3절.
카드는 챗봇 crud 로 직접 만든다 — A-1 이 만드는 행 모양 그대로다.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud import chat as chat_crud
from app.crud import evaluation as evaluation_crud
from app.crud import feedback as feedback_crud
from app.models.chat import ChatMessage
from app.models.enums import ChatMessageRole, ChatMessageStatus, MealStatus, SafetyStatus
from app.tests.factories import (
    make_food_ref,
    make_meal,
    make_meal_feedback,
    make_qqs_evaluation,
    make_user,
)

_SPEC_FIELDS = {
    "feedbackStatus", "summary", "reasoning",
    "suggestions", "expectedSatietyPct", "safetyStatus",
}
_TOFU = {"foodName": "두부 반 모", "advice": "단백질을 10g 더 채워요", "candidateFoodRefId": "KFD_TOFU"}
_EGG = {"foodName": "삶은 달걀 2개", "advice": "든든하게 채워요", "candidateFoodRefId": None}


def _h(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def _url(meal_id) -> str:
    return f"/api/v1/meals/{meal_id}/feedback/suggestions"


def _confirmed_meal(
    db: Session,
    user_id,
    *,
    suggestions: list[dict] | None = None,
    body: str | None = "라면 국물에서 나트륨이 많이 나왔어요.",
    safety: SafetyStatus = SafetyStatus.SAFE,
    with_feedback: bool = True,
    with_evaluation: bool = True,
):
    """확정된 끼니 — 점수 행과 (기본) READY · SAFE 피드백 행이 있다."""
    meal = make_meal(db, user_id=user_id, status=MealStatus.EVALUATED)
    if with_evaluation:
        make_qqs_evaluation(db, meal_id=meal.id)
    if with_feedback:
        make_meal_feedback(
            db, user_id=user_id, meal_id=meal.id,
            body=body, safety_status=safety, suggestions=suggestions,
        )
    return meal


def _card(
    db: Session,
    *,
    user_id,
    meal_id,
    food_name: str = "쌈채소 한 접시",
    advice: str = "식이섬유를 채워요",
    food_ref_id: str | None = "KFD_SALAD",
):
    """열린 대화를 닫고 새 대화 · 메시지 · 카드 1장. 카드 시각은 점수 행보다 뒤다
    (`computed_at` 은 트랜잭션 시작 시각 `now()`, 메시지는 `clock_timestamp()`)."""
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
        food_name=food_name, advice=advice, food_ref_id=food_ref_id,
    )


def _make_stale(db: Session, meal, card) -> None:
    """카드를 만든 뒤 끼니를 고쳐 재확정한 상태 — 점수 행 시각을 카드보다 뒤로 민다."""
    evaluation = evaluation_crud.get_by_meal(db, meal.id)
    evaluation.computed_at = db.get(ChatMessage, card.message_id).created_at + timedelta(seconds=1)
    db.flush()


def _put(client: TestClient, user_id, meal_id, card_id):
    return client.put(_url(meal_id), headers=_h(user_id), json={"foodCardId": str(card_id)})


# ── 정상 ────────────────────────────────────────────────────


def test_replaces_all_suggestions_with_the_card(client: TestClient, db: Session) -> None:
    user = make_user(db)
    make_food_ref(
        db, food_ref_id="KFD_SALAD", name="쌈채소", fiber_g=2.1,
        calories=None, protein_g=None, fat_g=None, carbohydrate_g=None, sodium_mg=None,
    )
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU, _EGG])
    card = _card(db, user_id=user.id, meal_id=meal.id)

    res = _put(client, user.id, meal.id, card.id)

    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert set(data) == _SPEC_FIELDS
    assert data["feedbackStatus"] == "READY"
    assert data["suggestions"] == [
        {
            "foodName": "쌈채소 한 접시",
            "advice": "식이섬유를 채워요",
            "nutrients": [{"code": "FIBER", "amount": 2.1, "unit": "g"}],
        }
    ]


def test_stores_the_card_in_the_ai_contract_shape(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    card = _card(db, user_id=user.id, meal_id=meal.id)

    _put(client, user.id, meal.id, card.id)

    row = feedback_crud.get_by_meal(db, meal.id)
    db.refresh(row)
    assert row.suggestions == [
        {"foodName": "쌈채소 한 접시", "advice": "식이섬유를 채워요", "candidateFoodRefId": "KFD_SALAD"}
    ]


def test_get_feedback_and_meal_detail_show_the_replaced_suggestion(
    client: TestClient, db: Session
) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU, _EGG])
    card = _card(db, user_id=user.id, meal_id=meal.id)
    _put(client, user.id, meal.id, card.id)

    feedback = client.get(f"/api/v1/meals/{meal.id}/feedback", headers=_h(user.id)).json()["data"]
    detail = client.get(f"/api/v1/meals/{meal.id}", headers=_h(user.id)).json()["data"]

    assert [s["foodName"] for s in feedback["suggestions"]] == ["쌈채소 한 접시"]
    assert [s["foodName"] for s in detail["feedback"]["suggestions"]] == ["쌈채소 한 접시"]


def test_leaves_body_reasoning_safety_and_scores_untouched(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    row = feedback_crud.get_by_meal(db, meal.id)
    evaluation = evaluation_crud.get_by_meal(db, meal.id)
    before_row = (row.body, row.reasoning, row.safety_status, row.model_version)
    before_scores = (evaluation.quantity_score, evaluation.quality_score, evaluation.satiety_score)
    card = _card(db, user_id=user.id, meal_id=meal.id)

    _put(client, user.id, meal.id, card.id)

    db.refresh(row)
    db.refresh(evaluation)
    assert (row.body, row.reasoning, row.safety_status, row.model_version) == before_row
    assert (
        evaluation.quantity_score, evaluation.quality_score, evaluation.satiety_score
    ) == before_scores


def test_same_card_twice_is_ok(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    card = _card(db, user_id=user.id, meal_id=meal.id)

    first = _put(client, user.id, meal.id, card.id)
    second = _put(client, user.id, meal.id, card.id)

    assert first.status_code == second.status_code == 200
    assert first.json()["data"] == second.json()["data"]


def test_card_from_closed_conversation_is_accepted(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    card = _card(db, user_id=user.id, meal_id=meal.id)
    chat_crud.close_open_conversation(db, user.id)

    res = _put(client, user.id, meal.id, card.id)

    assert res.status_code == 200, res.text


def test_card_without_food_ref_has_empty_nutrients(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    card = _card(db, user_id=user.id, meal_id=meal.id, food_ref_id=None)

    data = _put(client, user.id, meal.id, card.id).json()["data"]

    assert data["suggestions"] == [
        {"foodName": "쌈채소 한 접시", "advice": "식이섬유를 채워요", "nutrients": []}
    ]


def test_feedback_without_suggestions_can_be_replaced(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=None)
    card = _card(db, user_id=user.id, meal_id=meal.id)

    data = _put(client, user.id, meal.id, card.id).json()["data"]

    assert [s["foodName"] for s in data["suggestions"]] == ["쌈채소 한 접시"]


# ── 404 NOT_FOUND ───────────────────────────────────────────


def _assert_error(res, status: int, code: str) -> None:
    assert res.status_code == status, res.text
    body = res.json()
    assert body["success"] is False
    assert body["error"]["code"] == code


def test_unknown_meal_is_404(client: TestClient, db: Session) -> None:
    user = make_user(db)

    _assert_error(_put(client, user.id, uuid.uuid4(), uuid.uuid4()), 404, "NOT_FOUND")


def test_someone_elses_meal_is_404(client: TestClient, db: Session) -> None:
    owner = make_user(db)
    stranger = make_user(db, nickname="남")
    meal = _confirmed_meal(db, owner.id, suggestions=[_TOFU])
    card = _card(db, user_id=owner.id, meal_id=meal.id)

    _assert_error(_put(client, stranger.id, meal.id, card.id), 404, "NOT_FOUND")


def test_deleted_meal_is_404(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    card = _card(db, user_id=user.id, meal_id=meal.id)
    meal.deleted_at = datetime.now(UTC)
    db.flush()

    _assert_error(_put(client, user.id, meal.id, card.id), 404, "NOT_FOUND")


def test_unknown_card_is_404(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])

    _assert_error(_put(client, user.id, meal.id, uuid.uuid4()), 404, "NOT_FOUND")


def test_someone_elses_card_on_my_meal_is_404(client: TestClient, db: Session) -> None:
    owner = make_user(db)
    stranger = make_user(db, nickname="남")
    meal = _confirmed_meal(db, owner.id, suggestions=[_TOFU])
    card = _card(db, user_id=stranger.id, meal_id=meal.id)

    _assert_error(_put(client, owner.id, meal.id, card.id), 404, "NOT_FOUND")


def test_card_from_another_meal_conversation_is_404(client: TestClient, db: Session) -> None:
    user = make_user(db)
    lunch = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    dinner = _confirmed_meal(db, user.id, suggestions=[_EGG])
    card = _card(db, user_id=user.id, meal_id=lunch.id)

    _assert_error(_put(client, user.id, dinner.id, card.id), 404, "NOT_FOUND")


def test_card_from_conversation_without_meal_is_404(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    card = _card(db, user_id=user.id, meal_id=None)

    _assert_error(_put(client, user.id, meal.id, card.id), 404, "NOT_FOUND")


# ── 409 NOT_CONFIRMED ───────────────────────────────────────


@pytest.mark.parametrize("status", [MealStatus.ANALYZING, MealStatus.REVIEW_REQUIRED])
def test_unconfirmed_meal_is_not_confirmed(client: TestClient, db: Session, status) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    card = _card(db, user_id=user.id, meal_id=meal.id)
    meal.status = status
    db.flush()

    _assert_error(_put(client, user.id, meal.id, card.id), 409, "NOT_CONFIRMED")


# ── 409 CONFLICT ────────────────────────────────────────────


def test_card_older_than_last_confirm_is_conflict(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    card = _card(db, user_id=user.id, meal_id=meal.id)
    _make_stale(db, meal, card)

    _assert_error(_put(client, user.id, meal.id, card.id), 409, "CONFLICT")


def test_missing_evaluation_is_conflict(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU], with_evaluation=False)
    card = _card(db, user_id=user.id, meal_id=meal.id)

    _assert_error(_put(client, user.id, meal.id, card.id), 409, "CONFLICT")


def test_missing_feedback_row_is_conflict(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, with_feedback=False)
    card = _card(db, user_id=user.id, meal_id=meal.id)

    _assert_error(_put(client, user.id, meal.id, card.id), 409, "CONFLICT")


@pytest.mark.parametrize("body", [None, "   "], ids=["본문 없음", "공백 본문"])
def test_feedback_without_body_is_conflict(client: TestClient, db: Session, body) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU], body=body)
    card = _card(db, user_id=user.id, meal_id=meal.id)

    _assert_error(_put(client, user.id, meal.id, card.id), 409, "CONFLICT")


@pytest.mark.parametrize("safety", [SafetyStatus.BLOCKED, SafetyStatus.REVIEW_REQUIRED])
def test_unsafe_feedback_is_conflict(client: TestClient, db: Session, safety) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU], safety=safety)
    card = _card(db, user_id=user.id, meal_id=meal.id)

    _assert_error(_put(client, user.id, meal.id, card.id), 409, "CONFLICT")


def test_conflict_leaves_suggestions_untouched(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU, _EGG])
    card = _card(db, user_id=user.id, meal_id=meal.id)
    _make_stale(db, meal, card)

    _put(client, user.id, meal.id, card.id)

    row = feedback_crud.get_by_meal(db, meal.id)
    db.refresh(row)
    assert row.suggestions == [_TOFU, _EGG]


# ── 확인 순서 ───────────────────────────────────────────────


def test_someone_elses_meal_wins_over_card_checks(client: TestClient, db: Session) -> None:
    """남의 끼니면 카드가 무엇이든 404 — 카드 존재 여부를 흘리지 않는다."""
    owner = make_user(db)
    stranger = make_user(db, nickname="남")
    meal = _confirmed_meal(db, owner.id, suggestions=[_TOFU])

    _assert_error(_put(client, stranger.id, meal.id, uuid.uuid4()), 404, "NOT_FOUND")


def test_unconfirmed_wins_over_stale_card(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    card = _card(db, user_id=user.id, meal_id=meal.id)
    _make_stale(db, meal, card)
    meal.status = MealStatus.ANALYZING
    db.flush()

    _assert_error(_put(client, user.id, meal.id, card.id), 409, "NOT_CONFIRMED")


def test_unconfirmed_wins_over_unknown_card(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])
    meal.status = MealStatus.ANALYZING
    db.flush()

    _assert_error(_put(client, user.id, meal.id, uuid.uuid4()), 409, "NOT_CONFIRMED")


# ── 계약 ────────────────────────────────────────────────────


def test_typo_field_is_422(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])

    res = client.put(_url(meal.id), headers=_h(user.id), json={"foodCardID": str(uuid.uuid4())})

    _assert_error(res, 422, "VALIDATION_ERROR")


def test_invalid_uuid_is_422(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])

    res = client.put(_url(meal.id), headers=_h(user.id), json={"foodCardId": "not-a-uuid"})

    _assert_error(res, 422, "VALIDATION_ERROR")


def test_missing_header_is_401(client: TestClient, db: Session) -> None:
    user = make_user(db)
    meal = _confirmed_meal(db, user.id, suggestions=[_TOFU])

    res = client.put(_url(meal.id), json={"foodCardId": str(uuid.uuid4())})

    assert res.status_code == 401
