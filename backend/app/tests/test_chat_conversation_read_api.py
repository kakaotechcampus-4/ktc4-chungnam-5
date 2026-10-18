"""GET /chat/conversations/{conversationId} (A-3) 의 HTTP 계약.

FE 가 실제로 보는 모양 — 200 · 응답 래퍼 · camelCase · 404 단일화 · 가드레일 코드.
동작의 세부(정렬 · 카드 · 기준 끼니)는 `test_chat_conversation_read.py` 가 본다.
"""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crud import chat as chat_crud
from app.models.enums import ChatMessageRole, ChatMessageStatus, SafetyStatus
from app.tests.factories import make_user

_URL = "/api/v1/chat/conversations"
_MESSAGE_FIELDS = {
    "messageId", "role", "status", "text", "foodCards", "safetyStatus", "createdAt",
}


def _h(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def _ask(db: Session, conversation_id, *, reply_safety: SafetyStatus | None):
    """질문 하나와 그 답. `reply_safety` 가 None 이면 답이 아직 생성 중이다."""
    chat_crud.add_message(
        db, conversation_id=conversation_id, role=ChatMessageRole.USER,
        status=ChatMessageStatus.READY, text="질문", safety_status=None,
    )
    chat_crud.add_message(
        db, conversation_id=conversation_id, role=ChatMessageRole.ASSISTANT,
        status=ChatMessageStatus.GENERATING if reply_safety is None else ChatMessageStatus.READY,
        text="답" if reply_safety is SafetyStatus.SAFE else None,
        safety_status=reply_safety,
    )


def test_returns_conversation(client: TestClient, db: Session):
    user = make_user(db)
    conversation = chat_crud.create_conversation(db, user_id=user.id, meal_id=None)
    _ask(db, conversation.id, reply_safety=SafetyStatus.SAFE)

    res = client.get(f"{_URL}/{conversation.id}", headers=_h(user.id))

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True and body["error"] is None
    data = body["data"]
    assert set(data) == {"conversationId", "mealId", "messages"}
    assert data["conversationId"] == str(conversation.id)
    assert data["mealId"] is None
    question, reply = data["messages"]
    assert set(question) == set(reply) == _MESSAGE_FIELDS
    assert (question["role"], question["safetyStatus"]) == ("USER", None)
    assert (reply["role"], reply["status"], reply["text"]) == ("ASSISTANT", "READY", "답")
    assert reply["foodCards"] == []
    assert reply["createdAt"].endswith("+09:00")


def test_generating_reply_is_shown_as_is(client: TestClient, db: Session):
    user = make_user(db)
    conversation = chat_crud.create_conversation(db, user_id=user.id, meal_id=None)
    _ask(db, conversation.id, reply_safety=None)

    res = client.get(f"{_URL}/{conversation.id}", headers=_h(user.id))

    assert res.status_code == 200
    reply = res.json()["data"]["messages"][-1]
    assert (reply["status"], reply["text"], reply["safetyStatus"]) == ("GENERATING", None, None)


def test_blocked_last_reply_carries_medical_code(client: TestClient, db: Session):
    """명세: BLOCKED 면 `error.code = MEDICAL_QUESTION_DETECTED` (200). 폴링 대상인 마지막
    답이 막혔을 때 FE 가 `error.code` 하나로 상담 안내를 띄운다."""
    user = make_user(db)
    conversation = chat_crud.create_conversation(db, user_id=user.id, meal_id=None)
    _ask(db, conversation.id, reply_safety=SafetyStatus.BLOCKED)

    res = client.get(f"{_URL}/{conversation.id}", headers=_h(user.id))

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["error"]["code"] == "MEDICAL_QUESTION_DETECTED"
    reply = body["data"]["messages"][-1]
    assert (reply["safetyStatus"], reply["text"], reply["foodCards"]) == ("BLOCKED", None, [])


def test_earlier_blocked_reply_does_not_carry_code(client: TestClient, db: Session):
    """지난 BLOCKED 때문에 이후 조회마다 코드가 붙으면 FE 가 매번 상담 안내를 띄운다."""
    user = make_user(db)
    conversation = chat_crud.create_conversation(db, user_id=user.id, meal_id=None)
    _ask(db, conversation.id, reply_safety=SafetyStatus.BLOCKED)
    _ask(db, conversation.id, reply_safety=SafetyStatus.SAFE)

    res = client.get(f"{_URL}/{conversation.id}", headers=_h(user.id))

    assert res.status_code == 200
    assert res.json()["error"] is None


def test_others_conversation_is_404(client: TestClient, db: Session):
    owner = make_user(db)
    stranger = make_user(db, nickname="남")
    conversation = chat_crud.create_conversation(db, user_id=owner.id, meal_id=None)

    res = client.get(f"{_URL}/{conversation.id}", headers=_h(stranger.id))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_missing_conversation_is_404(client: TestClient, db: Session):
    user = make_user(db)

    res = client.get(f"{_URL}/{uuid.uuid4()}", headers=_h(user.id))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_malformed_conversation_id_is_422(client: TestClient, db: Session):
    user = make_user(db)

    res = client.get(f"{_URL}/not-a-uuid", headers=_h(user.id))

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_missing_user_header_is_401(client: TestClient):
    res = client.get(f"{_URL}/{uuid.uuid4()}")

    assert res.status_code == 401


# --- A-4 GET /chat/conversations/latest ---


def test_latest_without_conversation_is_200_null(client: TestClient, db: Session):
    """명세: 대화가 없으면 `data: null` (200). `latest` 가 UUID 경로에 잡혀 422 가 나면 안 된다
    — 라우트 등록 순서를 지킨다."""
    user = make_user(db)

    res = client.get(f"{_URL}/latest", headers=_h(user.id))

    assert res.status_code == 200
    assert res.json() == {"success": True, "data": None, "error": None}


def test_latest_returns_open_conversation(client: TestClient, db: Session):
    user = make_user(db)
    conversation = chat_crud.create_conversation(db, user_id=user.id, meal_id=None)
    _ask(db, conversation.id, reply_safety=SafetyStatus.SAFE)

    res = client.get(f"{_URL}/latest", headers=_h(user.id))

    assert res.status_code == 200
    data = res.json()["data"]
    assert data["conversationId"] == str(conversation.id)
    assert [m["role"] for m in data["messages"]] == ["USER", "ASSISTANT"]


def test_latest_blocked_last_reply_carries_medical_code(client: TestClient, db: Session):
    user = make_user(db)
    conversation = chat_crud.create_conversation(db, user_id=user.id, meal_id=None)
    _ask(db, conversation.id, reply_safety=SafetyStatus.BLOCKED)

    res = client.get(f"{_URL}/latest", headers=_h(user.id))

    assert res.status_code == 200
    assert res.json()["error"]["code"] == "MEDICAL_QUESTION_DETECTED"


def test_latest_missing_user_header_is_401(client: TestClient):
    res = client.get(f"{_URL}/latest")

    assert res.status_code == 401
