"""POST /chat/conversations (A-1) 의 HTTP 계약.

FE 가 실제로 보는 모양 — 201 · 응답 래퍼 · camelCase · 명세 필드 · 404 단일화 · 422.
동작의 세부(기준 끼니 고르기 · 닫기)는 `test_chat_service.py` 가 본다.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.chat import ChatConversation
from app.models.enums import MealStatus
from app.tests.factories import make_food_ref, make_meal, make_meal_feedback, make_user

_URL = "/api/v1/chat/conversations"
_MESSAGE_FIELDS = {
    "messageId", "role", "status", "text", "foodCards", "safetyStatus", "createdAt",
}


def _h(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def _meal_with_card(db: Session, user_id: uuid.UUID):
    make_food_ref(db, food_ref_id="KFD_TOFU", name="두부", protein_g=8, fiber_g=1)
    meal = make_meal(db, user_id=user_id, status=MealStatus.EVALUATED)
    make_meal_feedback(
        db,
        user_id=user_id,
        meal_id=meal.id,
        suggestions=[{"foodName": "두부 반 모", "advice": "단백질을 채워요", "candidateFoodRefId": "KFD_TOFU"}],
    )
    return meal


def test_creates_conversation_with_intro_card(client: TestClient, db: Session):
    user = make_user(db)
    meal = _meal_with_card(db, user.id)

    res = client.post(_URL, json={"mealId": str(meal.id)}, headers=_h(user.id))

    assert res.status_code == 201
    body = res.json()
    assert body["success"] is True and body["error"] is None
    data = body["data"]
    assert set(data) == {"conversationId", "mealId", "messages"}
    assert data["mealId"] == str(meal.id)

    [message] = data["messages"]
    assert set(message) == _MESSAGE_FIELDS
    assert message["role"] == "ASSISTANT"
    assert message["status"] == "READY"
    assert message["safetyStatus"] == "SAFE"
    assert message["text"] == "다음 끼니로 이 음식은 어떤가요?"
    assert message["createdAt"].endswith("+09:00")

    [card] = message["foodCards"]
    assert set(card) == {"foodCardId", "foodName", "effects"}
    assert card["foodName"] == "두부 반 모"
    assert {"code": "PROTEIN", "amount": 8.0, "unit": "g"} in card["effects"]


def test_no_body_and_empty_body_both_work(client: TestClient, db: Session):
    """FE 가 AI 탭에서 바로 열 때는 body 없이 부른다."""
    user = make_user(db)

    no_body = client.post(_URL, headers=_h(user.id))
    empty = client.post(_URL, json={}, headers=_h(user.id))

    assert no_body.status_code == empty.status_code == 201
    assert empty.json()["data"] == {
        "conversationId": empty.json()["data"]["conversationId"],
        "mealId": None,
        "messages": [],
    }


def test_others_meal_is_404_and_creates_nothing(client: TestClient, db: Session):
    user = make_user(db)
    stranger = make_user(db, nickname="남")
    meal = _meal_with_card(db, stranger.id)

    res = client.post(_URL, json={"mealId": str(meal.id)}, headers=_h(user.id))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
    assert db.scalar(select(func.count()).select_from(ChatConversation)) == 0


def test_typo_field_is_422(client: TestClient, db: Session):
    user = make_user(db)

    res = client.post(_URL, json={"mealID": str(uuid.uuid4())}, headers=_h(user.id))

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_malformed_meal_id_is_422(client: TestClient, db: Session):
    user = make_user(db)

    res = client.post(_URL, json={"mealId": "not-a-uuid"}, headers=_h(user.id))

    assert res.status_code == 422


def test_missing_user_header_is_401(client: TestClient):
    res = client.post(_URL, json={})

    assert res.status_code == 401


def test_unknown_user_is_404_user_not_found(client: TestClient):
    res = client.post(_URL, json={}, headers=_h(uuid.uuid4()))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "USER_NOT_FOUND"
