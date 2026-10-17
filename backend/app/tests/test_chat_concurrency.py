"""`lock_user` 의 동시성 — 커넥션이 둘 필요해 실제로 커밋하는 `sessions` 를 쓴다.

`sessions` 는 `task_queue` 만 비우므로 여기서 만든 사용자는 테스트가 직접 지운다
(대화 · 끼니 등은 `ON DELETE CASCADE` 로 따라 지워진다).
"""

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.crud import chat as chat_crud
from app.models.chat import ChatConversation
from app.models.enums import MedicationStage
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
