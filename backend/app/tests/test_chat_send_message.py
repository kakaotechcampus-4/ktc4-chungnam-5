"""`services/chat.py::send_message` — A-2 사용자 메시지 전송의 동작.

HTTP 모양(camelCase · 202 · 에러 래퍼 · 422)은 `test_chat_messages_api.py` 가 본다.
"""

import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.crud import chat as chat_crud
from app.models.chat import ChatMessage
from app.models.enums import ChatMessageRole, ChatMessageStatus, SafetyStatus
from app.models.task import Task
from app.services import chat as chat_service
from app.tests.factories import make_user


def _open(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    return chat_service.start_conversation(db, user_id=user_id, meal_id=None).conversation_id


def _messages(db: Session, conversation_id: uuid.UUID) -> list[ChatMessage]:
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at)
        )
    )


def _reply_tasks(db: Session) -> list[Task]:
    return list(db.scalars(select(Task).where(Task.type == "chat.reply")))


def _age(db: Session, message_id: uuid.UUID, seconds: int) -> None:
    """메시지를 `seconds` 초 전에 만든 것으로 되돌린다."""
    db.execute(
        update(ChatMessage)
        .where(ChatMessage.id == message_id)
        .values(created_at=func.clock_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, seconds))
    )
    db.expire_all()


def test_saves_user_message_and_generating_reply_and_enqueues(db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)

    result = chat_service.send_message(
        db, user_id=user.id, conversation_id=conversation_id, text="다른 음식 추천해줘"
    )

    question, reply = _messages(db, conversation_id)
    assert (question.role, question.status, question.text, question.safety_status) == (
        ChatMessageRole.USER, ChatMessageStatus.READY, "다른 음식 추천해줘", None,
    )
    assert (reply.role, reply.status, reply.text, reply.safety_status) == (
        ChatMessageRole.ASSISTANT, ChatMessageStatus.GENERATING, None, None,
    )
    assert question.created_at < reply.created_at

    assert result.user_message.message_id == question.id
    assert result.user_message.food_cards == []
    assert result.assistant_message_id == reply.id
    assert (result.poll_interval_ms, result.timeout_ms) == (1000, 30000)

    [task] = _reply_tasks(db)
    assert task.payload == {"messageId": str(reply.id)}


def test_others_conversation_is_not_found(db: Session):
    owner = make_user(db)
    stranger = make_user(db, nickname="남")
    conversation_id = _open(db, owner.id)

    with pytest.raises(chat_service.ConversationNotFoundError):
        chat_service.send_message(db, user_id=stranger.id, conversation_id=conversation_id, text="안녕")


def test_closed_conversation_is_not_found(db: Session):
    user = make_user(db)
    old = _open(db, user.id)
    _open(db, user.id)  # "＋ 새 대화" 가 old 를 닫는다

    with pytest.raises(chat_service.ConversationNotFoundError):
        chat_service.send_message(db, user_id=user.id, conversation_id=old, text="안녕")


def test_unknown_conversation_is_not_found(db: Session):
    user = make_user(db)

    with pytest.raises(chat_service.ConversationNotFoundError):
        chat_service.send_message(db, user_id=user.id, conversation_id=uuid.uuid4(), text="안녕")


def test_reply_in_progress_is_rejected_and_writes_nothing(db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)
    chat_service.send_message(db, user_id=user.id, conversation_id=conversation_id, text="첫 질문")

    with pytest.raises(chat_service.ReplyInProgressError):
        chat_service.send_message(db, user_id=user.id, conversation_id=conversation_id, text="둘째 질문")

    assert len(_messages(db, conversation_id)) == 2
    assert len(_reply_tasks(db)) == 1


def test_stale_generating_reply_is_failed_and_send_proceeds(db: Session):
    """워커가 죽어 갇힌 답변 때문에 대화가 영영 막히면 안 된다."""
    user = make_user(db)
    conversation_id = _open(db, user.id)
    first = chat_service.send_message(db, user_id=user.id, conversation_id=conversation_id, text="첫 질문")
    # 질문도 같이 늙혀야 `created_at` 순서가 유지된다 (답변만 옛날로 돌리면 정렬이 뒤집힌다).
    _age(db, first.user_message.message_id, chat_crud.CHAT_REPLY_STALE_SEC + 2)
    _age(db, first.assistant_message_id, chat_crud.CHAT_REPLY_STALE_SEC + 1)

    chat_service.send_message(db, user_id=user.id, conversation_id=conversation_id, text="다시 질문")

    statuses = [(m.role, m.status) for m in _messages(db, conversation_id)]
    assert statuses == [
        (ChatMessageRole.USER, ChatMessageStatus.READY),
        (ChatMessageRole.ASSISTANT, ChatMessageStatus.FAILED),
        (ChatMessageRole.USER, ChatMessageStatus.READY),
        (ChatMessageRole.ASSISTANT, ChatMessageStatus.GENERATING),
    ]


def test_generating_reply_just_inside_window_still_conflicts(db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)
    first = chat_service.send_message(db, user_id=user.id, conversation_id=conversation_id, text="첫 질문")
    _age(db, first.assistant_message_id, chat_crud.CHAT_REPLY_STALE_SEC - 10)

    with pytest.raises(chat_service.ReplyInProgressError):
        chat_service.send_message(db, user_id=user.id, conversation_id=conversation_id, text="둘째")


def test_finished_reply_does_not_block_next_message(db: Session):
    user = make_user(db)
    conversation_id = _open(db, user.id)
    first = chat_service.send_message(db, user_id=user.id, conversation_id=conversation_id, text="첫 질문")
    reply = db.get(ChatMessage, first.assistant_message_id)
    chat_crud.finish_message(
        db, reply, status=ChatMessageStatus.READY, text="답", safety_status=SafetyStatus.SAFE
    )

    chat_service.send_message(db, user_id=user.id, conversation_id=conversation_id, text="둘째")

    assert len(_messages(db, conversation_id)) == 4
