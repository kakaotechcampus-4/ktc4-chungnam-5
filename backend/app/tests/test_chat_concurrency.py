"""`lock_user` 의 동시성 — 커넥션이 둘 필요해 실제로 커밋하는 `sessions` 를 쓴다.

`sessions` 는 `task_queue` 만 비우므로 여기서 만든 사용자는 테스트가 직접 지운다
(대화 · 끼니 등은 `ON DELETE CASCADE` 로 따라 지워진다).
"""

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.crud import chat as chat_crud
from app.models.chat import ChatConversation, ChatMessage
from app.models.enums import ChatMessageRole, ChatMessageStatus, MedicationStage, SafetyStatus
from app.models.medication import MedicationSnapshot
from app.models.user import User
from app.services import chat as chat_service


@pytest.fixture
def committed_user(sessions: sessionmaker[Session]) -> Iterator[uuid.UUID]:
    with sessions() as setup:
        user = User(nickname="동시성", height_cm=Decimal("174.0"), baseline_meal_kcal=Decimal("700.00"))
        setup.add(user)
        setup.commit()
        user_id = user.id
    try:
        yield user_id
    finally:
        with sessions() as cleanup:
            cleanup.execute(delete(User).where(User.id == user_id))
            cleanup.commit()


def test_concurrent_starts_for_same_user_both_succeed_one_stays_open(
    sessions: sessionmaker[Session], committed_user: uuid.UUID
):
    """더블탭 — 락이 없으면 둘 다 "열린 대화 없음" 을 보고 INSERT 해 둘째가 500 이다."""
    holder = sessions()
    try:
        assert chat_crud.lock_user(holder, committed_user) is True

        def second_tap():
            with sessions() as other:
                return chat_service.start_conversation(other, user_id=committed_user, meal_id=None)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(second_tap)
            # 첫째가 락을 쥐고 있는 동안 둘째는 끝나지 못한다.
            with pytest.raises(TimeoutError):
                future.result(timeout=1)

            chat_crud.close_open_conversation(holder, committed_user)
            chat_crud.create_conversation(holder, user_id=committed_user, meal_id=None)
            holder.commit()

            future.result(timeout=10)  # 예외가 없어야 한다
    finally:
        holder.close()

    with sessions() as check:
        base = ChatConversation.user_id == committed_user
        total = check.scalar(select(func.count()).select_from(ChatConversation).where(base))
        open_ = check.scalar(
            select(func.count())
            .select_from(ChatConversation)
            .where(base, ChatConversation.closed_at.is_(None))
        )
    assert (total, open_) == (2, 1)


def test_lock_user_does_not_block_fk_child_writes(
    sessions: sessionmaker[Session], committed_user: uuid.UUID
):
    """`FOR UPDATE` 면 다른 트랜잭션의 `users` FK 검사(KEY SHARE)가 막혀 워커와 교착한다.
    `FOR NO KEY UPDATE` 라 막히지 않아야 한다."""
    holder = sessions()
    try:
        assert chat_crud.lock_user(holder, committed_user) is True

        with sessions() as other:
            other.execute(text("SET LOCAL lock_timeout = '2s'"))
            other.add(MedicationSnapshot(user_id=committed_user, stage=MedicationStage.MAINTENANCE))
            other.commit()  # 락에 막히면 lock_timeout 으로 여기서 터진다
    finally:
        holder.rollback()
        holder.close()


def test_lock_user_reports_missing_row(sessions: sessionmaker[Session]):
    with sessions() as session:
        assert chat_crud.lock_user(session, uuid.uuid4()) is False


def test_concurrent_sends_to_same_conversation_second_is_rejected(
    sessions: sessionmaker[Session], committed_user: uuid.UUID
):
    """더블탭 — 대화 잠금이 없으면 둘 다 "GENERATING 없음" 을 보고 답변 자리표시를 둘 만든다."""
    with sessions() as setup:
        conversation_id = chat_service.start_conversation(
            setup, user_id=committed_user, meal_id=None
        ).conversation_id

    holder = sessions()
    try:
        assert chat_crud.lock_open_conversation(
            holder, user_id=committed_user, conversation_id=conversation_id
        ) is not None

        def second_tap():
            with sessions() as other:
                return chat_service.send_message(
                    other, user_id=committed_user, conversation_id=conversation_id, text="둘째"
                )

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(second_tap)
            with pytest.raises(TimeoutError):
                future.result(timeout=1)

            chat_crud.add_message(
                holder,
                conversation_id=conversation_id,
                role=ChatMessageRole.ASSISTANT,
                status=ChatMessageStatus.GENERATING,
                text=None,
                safety_status=None,
            )
            holder.commit()

            with pytest.raises(chat_service.ReplyInProgressError):
                future.result(timeout=10)
    finally:
        holder.close()


def test_expiry_path_does_not_overwrite_reply_the_worker_just_made_ready(
    sessions: sessionmaker[Session], committed_user: uuid.UUID
):
    """A-2 가 만료된 GENERATING 을 FAILED 로 닫으려는 순간 워커가 READY 를 커밋하는 경쟁.

    `find_generating_reply` 가 잠그지 않으면 A-2 는 낡은 GENERATING 을 보고 UPDATE 해
    워커가 막 쓴 READY 를 FAILED 로 덮는다. 잠그면 워커의 커밋을 기다렸다가 WHERE 를
    다시 평가해 그 답이 빠지고, 새 메시지는 정상으로 붙는다."""
    with sessions() as setup:
        conversation_id = chat_service.start_conversation(
            setup, user_id=committed_user, meal_id=None
        ).conversation_id
        reply_id = chat_service.send_message(
            setup, user_id=committed_user, conversation_id=conversation_id, text="첫 질문"
        ).assistant_message_id
        setup.execute(
            update(ChatMessage)
            .where(ChatMessage.id == reply_id)
            .values(
                created_at=func.clock_timestamp()
                - func.make_interval(0, 0, 0, 0, 0, 0, chat_crud.CHAT_REPLY_STALE_SEC + 1)
            )
        )
        setup.commit()

    holder = sessions()  # 워커 역할 — 답변을 잠근다
    try:
        message = chat_crud.lock_message_for_worker(holder, reply_id)
        assert message is not None

        def resend():
            with sessions() as other:
                return chat_service.send_message(
                    other, user_id=committed_user, conversation_id=conversation_id, text="둘째"
                )

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(resend)
            with pytest.raises(TimeoutError):
                future.result(timeout=1)

            chat_crud.finish_message(
                holder, message, status=ChatMessageStatus.READY, text="늦었지만 답", safety_status=SafetyStatus.SAFE
            )
            holder.commit()

            future.result(timeout=10)  # 예외 없이 접수된다
    finally:
        holder.close()

    with sessions() as check:
        reply = check.get(ChatMessage, reply_id)
        assert (reply.status, reply.text) == (ChatMessageStatus.READY, "늦었지만 답")
