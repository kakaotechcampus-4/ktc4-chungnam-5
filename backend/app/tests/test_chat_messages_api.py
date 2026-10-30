"""POST /chat/conversations/{conversationId}/messages (A-2) 의 HTTP 계약.

FE 가 실제로 보는 모양 — 202 · 응답 래퍼 · camelCase · 404 단일화 · 409 · 422.
동작의 세부(만료 · 큐 등록)는 `test_chat_send_message.py` 가 본다.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services import chat as chat_service
from app.tests.factories import make_user

_MESSAGE_FIELDS = {
    "messageId", "role", "status", "text", "foodCards", "safetyStatus", "createdAt",
}


def _url(conversation_id) -> str:
    return f"/api/v1/chat/conversations/{conversation_id}/messages"


def _h(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-Id": str(user_id)}


def _open(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    return chat_service.start_conversation(db, user_id=user_id, meal_id=None).conversation_id


def test_accepts_message(client: TestClient, db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)

    res = client.post(_url(conversation_id), json={"text": "  다른 음식 추천해줘  "}, headers=_h(user.id))

    assert res.status_code == 202
    body = res.json()
    assert body["success"] is True and body["error"] is None
    data = body["data"]
    assert set(data) == {"userMessage", "assistantMessageId", "pollIntervalMs", "timeoutMs"}
    assert (data["pollIntervalMs"], data["timeoutMs"]) == (1000, 30000)
    uuid.UUID(data["assistantMessageId"])

    message = data["userMessage"]
    assert set(message) == _MESSAGE_FIELDS
    assert message["role"] == "USER"
    assert message["status"] == "READY"
    assert message["text"] == "다른 음식 추천해줘"  # 앞뒤 공백은 지운다
    assert message["foodCards"] == []
    assert message["safetyStatus"] is None
    assert message["createdAt"].endswith("+09:00")


def test_500_chars_is_accepted(client: TestClient, db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)

    res = client.post(_url(conversation_id), json={"text": "가" * 500}, headers=_h(user.id))

    assert res.status_code == 202


def test_reply_in_progress_is_409(client: TestClient, db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)
    client.post(_url(conversation_id), json={"text": "첫 질문"}, headers=_h(user.id))

    res = client.post(_url(conversation_id), json={"text": "둘째"}, headers=_h(user.id))

    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"


def test_others_conversation_is_404(client: TestClient, db: Session):
    owner = make_user(db)
    stranger = make_user(db, nickname="남")
    conversation_id = _open(db, owner.id)

    res = client.post(_url(conversation_id), json={"text": "안녕"}, headers=_h(stranger.id))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_closed_conversation_is_404(client: TestClient, db: Session):
    user = make_user(db)
    old = _open(db, user.id)
    _open(db, user.id)

    res = client.post(_url(old), json={"text": "안녕"}, headers=_h(user.id))

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_conversation_is_404(client: TestClient, db: Session):
    user = make_user(db)

    res = client.post(_url(uuid.uuid4()), json={"text": "안녕"}, headers=_h(user.id))

    assert res.status_code == 404


@pytest.mark.parametrize(
    "body",
    [{}, {"text": ""}, {"text": "   "}, {"text": "가" * 501}, {"text": "안녕", "message": "오타"}],
    ids=["missing", "empty", "blank", "too-long", "typo-field"],
)
def test_invalid_body_is_422(client: TestClient, db: Session, body):
    user = make_user(db)
    conversation_id = _open(db, user.id)

    res = client.post(_url(conversation_id), json=body, headers=_h(user.id))

    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_malformed_conversation_id_is_422(client: TestClient, db: Session):
    user = make_user(db)

    res = client.post(_url("not-a-uuid"), json={"text": "안녕"}, headers=_h(user.id))

    assert res.status_code == 422


def test_missing_user_header_is_401(client: TestClient):
    res = client.post(_url(uuid.uuid4()), json={"text": "안녕"})

    assert res.status_code == 401
