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


# ─────────────────────────── 집기 ───────────────────────────


@pytest.fixture
def queue(sessions):
    from app.infra.queue import DbTaskQueue, QueueSettings

    return DbTaskQueue(
        sessions,
        settings=QueueSettings(QUEUE_MAX_ATTEMPTS=3, QUEUE_BACKOFF_BASE_SEC=30),
    )


def _put(sessions, **kwargs) -> Task:
    task = Task(type=kwargs.pop("type", "meal.analyze"), payload=kwargs.pop("payload", {"mealId": "m1"}), **kwargs)
    with sessions() as db:
        db.add(task)
        db.commit()
        db.refresh(task)
    return task


def test_claim_returns_none_on_empty_queue(queue):
    with queue.claim() as claim:
        assert claim is None


def test_success_commits_done_with_the_handler_result(sessions, queue):
    put = _put(sessions)

    with queue.claim() as claim:
        assert claim is not None
        assert claim.task.id == put.id
        assert claim.task.type == "meal.analyze"
        assert claim.task.payload == {"mealId": "m1"}
        assert claim.task.attempts == 0
        claim.result = {"items": 3}

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.DONE
        assert row.result == {"items": 3}
        assert row.finished_at is not None
        assert row.attempts == 0


def test_claimed_task_is_not_handed_out_twice(sessions, queue):
    """DONE 이 된 작업은 다시 집히지 않는다."""
    _put(sessions)

    with queue.claim() as claim:
        assert claim is not None

    with queue.claim() as claim:
        assert claim is None


# ─────────────────────────── 실패 ───────────────────────────


def test_failure_leaves_the_task_pending_and_records_the_attempt(sessions, queue):
    """실패하면 지우지도 완료하지도 않는다. 이력만 남기고 되돌린다."""
    put = _put(sessions)
    before = datetime.now(timezone.utc)

    with pytest.raises(RuntimeError):
        with queue.claim() as claim:
            assert claim is not None
            raise RuntimeError("AI 가 500 을 냈다")

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert row.attempts == 1
        assert "RuntimeError" in row.last_error
        assert row.result is None
        # 첫 실패 → 30초 뒤. 곧바로 다시 집으면 재시도가 순식간에 소진된다.
        assert row.next_run_at > before + timedelta(seconds=20)


def test_domain_writes_are_rolled_back_with_the_task(sessions, queue):
    """핸들러가 claim 의 세션에 쓴 것도 함께 되돌아간다.

    이게 SQS 대비 가장 큰 차이다 — 재시도할 때 이전 시도의 흔적이 남지 않는다.
    """
    put = _put(sessions)

    with pytest.raises(RuntimeError):
        with queue.claim() as claim:
            claim.db.add(Task(type="side.effect", payload={}))
            claim.db.flush()
            raise RuntimeError("처리 도중 실패")

    with sessions() as db:
        types = db.execute(select(Task.type)).scalars().all()
        assert types == ["meal.analyze"]
        assert _row(db, put.id).attempts == 1


def test_task_is_quarantined_after_max_attempts(sessions, queue):
    """3회째 실패하면 FAILED 로 옮기고 더 집지 않는다 — DLQ 자리다."""
    put = _put(sessions, attempts=2)

    with pytest.raises(RuntimeError):
        with queue.claim() as claim:
            raise RuntimeError("세 번째 실패")

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.FAILED
        assert row.attempts == 3

    with queue.claim() as claim:
        assert claim is None


def test_task_scheduled_in_the_future_is_not_claimed(sessions, queue):
    _put(sessions, next_run_at=datetime.now(timezone.utc) + timedelta(hours=1))

    with queue.claim() as claim:
        assert claim is None


# ─────────────────────────── 동시성 ───────────────────────────


def test_two_workers_never_claim_the_same_row(sessions, queue):
    """SKIP LOCKED — 워커 둘이 동시에 집으면 서로 다른 행을 가져간다.

    잠긴 행을 기다리지 않고 건너뛴다는 것이 핵심이다. 기다리면 워커를 늘려도
    한 줄로 서게 된다.
    """
    from app.infra.queue import DbTaskQueue

    first = _put(sessions, payload={"n": 1})
    second = _put(sessions, payload={"n": 2})
    other = DbTaskQueue(sessions)

    with queue.claim() as a, other.claim() as b:
        assert a is not None and b is not None
        assert {a.task.id, b.task.id} == {first.id, second.id}


def test_second_worker_gets_nothing_when_the_only_row_is_locked(sessions, queue):
    from app.infra.queue import DbTaskQueue

    _put(sessions)
    other = DbTaskQueue(sessions)

    with queue.claim() as a:
        assert a is not None
        with other.claim() as b:
            assert b is None
