"""DB 테이블 큐.

실제 PostgreSQL 을 상대로 돈다(conftest 의 testcontainer). SQLite 로 대체할 수 없다 —
이 큐의 전부가 `SELECT … FOR UPDATE SKIP LOCKED` 와 트랜잭션 경계이기 때문이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import TaskStatus
from app.models.task import Task


@pytest.fixture
def sessions(test_engine):
    """진짜로 커밋하는 세션 팩토리.

    conftest 의 `db` 픽스처는 savepoint 위에서 도는 단일 커넥션이라 여기 쓸 수 없다.
    이 테스트가 검증하는 게 바로 커밋·롤백 경계이고, 동시성 테스트에는 커넥션이 둘 필요하다.
    대신 테스트마다 테이블을 비운다.
    """
    factory = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    yield factory
    with factory() as cleanup:
        cleanup.execute(text("DELETE FROM task_queue"))
        cleanup.commit()


def _row(db: Session, task_id) -> Task:
    return db.execute(select(Task).where(Task.id == task_id)).scalar_one()


def test_new_task_defaults_to_pending_and_runnable_now(sessions):
    """넣기만 하면 곧바로 집을 수 있는 상태여야 한다."""
    with sessions() as db:
        task = Task(type="meal.analyze", payload={"mealId": "m1"})
        db.add(task)
        db.commit()
        db.refresh(task)

        assert task.status is TaskStatus.PENDING
        assert task.attempts == 0
        assert task.result is None
        assert task.last_error is None
        assert task.finished_at is None
        assert task.next_run_at <= datetime.now(timezone.utc) + timedelta(seconds=1)


# ─────────────────────────── 넣기 ───────────────────────────


def test_enqueue_is_atomic_with_the_domain_transaction(sessions):
    """도메인 커밋이 롤백되면 작업도 같이 사라진다.

    SQS 를 쓸 때는 이게 불가능해서 "커밋이 먼저다" 라는 규칙을 사람이 지켜야 했다.
    """
    from app.infra.queue import enqueue

    with sessions() as db:
        enqueue(db, "meal.analyze", {"mealId": "m1"})
        db.rollback()

    with sessions() as db:
        assert db.execute(select(Task)).scalars().all() == []


def test_enqueue_writes_the_row_on_commit(sessions):
    from app.infra.queue import enqueue

    with sessions() as db:
        enqueue(db, "meal.analyze", {"mealId": "m1"})
        db.commit()

    with sessions() as db:
        task = db.execute(select(Task)).scalar_one()
        assert task.type == "meal.analyze"
        assert task.payload == {"mealId": "m1"}
        assert task.status is TaskStatus.PENDING
