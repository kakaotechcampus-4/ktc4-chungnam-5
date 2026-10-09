"""DB 테이블 큐.

실제 PostgreSQL 을 상대로 돈다(conftest 의 testcontainer). SQLite 로 대체할 수 없다 —
이 큐의 전부가 `SELECT … FOR UPDATE SKIP LOCKED` 와 트랜잭션 경계이기 때문이다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.models.enums import TaskStatus
from app.models.task import Task


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


def test_new_task_has_no_lease(sessions):
    """lease 는 claim 이 쓴다. 막 넣은 작업에는 없다."""
    with sessions() as db:
        task = Task(type="meal.analyze", payload={"mealId": "m1"})
        db.add(task)
        db.commit()
        db.refresh(task)

        assert task.lease_token is None
        assert task.lease_expires_at is None


def test_processing_and_lease_columns_round_trip(sessions):
    token = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(minutes=2)
    with sessions() as db:
        task = Task(
            type="meal.analyze",
            payload={"mealId": "m1"},
            status=TaskStatus.PROCESSING,
            lease_token=token,
            lease_expires_at=expires,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    with sessions() as db:
        row = _row(db, task_id)
        assert row.status is TaskStatus.PROCESSING
        assert row.lease_token == token
        assert row.lease_expires_at == expires


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TaskStatus.PENDING, True),
        (TaskStatus.PROCESSING, True),
        (TaskStatus.DONE, False),
        (TaskStatus.FAILED, False),
    ],
)
def test_in_flight_means_not_finished(status, expected):
    """"생성 중" 판정과 중복 등록 방지는 대기 중과 처리 중을 똑같이 본다."""
    assert status.is_in_flight is expected


def test_lease_defaults_to_two_minutes():
    from app.infra.queue import QueueSettings

    assert QueueSettings(_env_file=None).QUEUE_LEASE_SEC == 120


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
        settings=QueueSettings(
            QUEUE_MAX_ATTEMPTS=3, QUEUE_BACKOFF_BASE_SEC=30, QUEUE_LEASE_SEC=120
        ),
    )


def _put(sessions, **kwargs) -> Task:
    task = Task(type=kwargs.pop("type", "meal.analyze"), payload=kwargs.pop("payload", {"mealId": "m1"}), **kwargs)
    with sessions() as db:
        db.add(task)
        db.commit()
        db.refresh(task)
    return task


def _expire(sessions, task_id) -> None:
    """lease 를 이미 지난 것으로 만든다. 워커가 죽어 120초가 흐른 상황이다."""
    with sessions() as db:
        db.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        db.commit()


def test_claim_returns_none_on_empty_queue(queue):
    assert queue.claim() is None


def test_claim_commits_processing_with_a_fresh_lease(sessions, queue):
    """집은 사실이 곧바로 커밋된다 — 다른 세션에서 PROCESSING 이 보여야 한다."""
    put = _put(sessions)
    before = datetime.now(timezone.utc)

    lease = queue.claim()

    assert lease is not None
    assert lease.task.id == put.id
    assert lease.task.type == "meal.analyze"
    assert lease.task.payload == {"mealId": "m1"}
    assert lease.task.attempts == 0  # 이번 시도 이전까지 집힌 횟수
    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PROCESSING
        assert row.attempts == 1
        assert row.lease_token == lease.token
        assert row.lease_expires_at > before + timedelta(seconds=100)


def test_claim_leaves_no_connection_checked_out(sessions, queue, test_engine):
    """claim 이 끝나면 트랜잭션도 커넥션도 남지 않는다 — 이 변경의 목적이다."""
    _put(sessions)

    assert queue.claim() is not None
    assert test_engine.pool.checkedout() == 0


def test_claimed_task_is_not_handed_out_twice(sessions, queue):
    _put(sessions)

    assert queue.claim() is not None
    assert queue.claim() is None


def test_claim_skips_a_row_another_worker_is_claiming(sessions, queue):
    """SKIP LOCKED — 다른 워커가 집는 중(행 잠금)인 행은 기다리지 않고 건너뛴다.

    기다리면 워커를 늘려도 한 줄로 서게 된다.
    """
    first = _put(sessions, payload={"n": 1})
    second = _put(sessions, payload={"n": 2})

    with sessions() as other:
        other.execute(select(Task).where(Task.id == first.id).with_for_update())
        lease = queue.claim()
        other.rollback()

    assert lease is not None
    assert lease.task.id == second.id


def test_task_scheduled_in_the_future_is_not_claimed(sessions, queue):
    _put(sessions, next_run_at=datetime.now(timezone.utc) + timedelta(hours=1))

    assert queue.claim() is None


# ─────────────────────────── 읽기 ───────────────────────────


def test_read_session_never_commits(sessions, queue):
    """`load` 단계 세션은 끝나면 롤백된다 — 실수로 쓴 것도 남지 않는다."""
    _put(sessions)

    with queue.read() as db:
        db.add(Task(type="side.effect", payload={}))
        db.flush()

    with sessions() as db:
        assert db.execute(select(Task.type)).scalars().all() == ["meal.analyze"]


# ─────────────────────────── 완료 ───────────────────────────


def test_complete_commits_done_with_the_result_and_clears_the_lease(sessions, queue):
    put = _put(sessions)
    lease = queue.claim()

    with queue.complete(lease) as done:
        done.result = {"items": 3}

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.DONE
        assert row.result == {"items": 3}
        assert row.finished_at is not None
        assert row.attempts == 1
        assert row.lease_token is None
        assert row.lease_expires_at is None


def test_complete_commits_domain_writes_with_done(sessions, queue):
    """도메인 쓰기와 DONE 은 한 트랜잭션이다."""
    put = _put(sessions)
    lease = queue.claim()

    with queue.complete(lease) as done:
        done.db.add(Task(type="side.effect", payload={}))

    with sessions() as db:
        assert sorted(db.execute(select(Task.type)).scalars().all()) == ["meal.analyze", "side.effect"]
        assert _row(db, put.id).status is TaskStatus.DONE


def test_complete_with_a_lost_lease_rolls_back_domain_writes(sessions, queue):
    """lease 가 만료돼 다른 워커가 가져갔으면 이 워커의 결과는 버린다 — 도메인 쓰기까지."""
    from app.infra.queue import LeaseLostError

    put = _put(sessions)
    lease = queue.claim()
    other_token = uuid.uuid4()
    with sessions() as db:
        db.execute(update(Task).where(Task.id == put.id).values(lease_token=other_token))
        db.commit()

    with pytest.raises(LeaseLostError):
        with queue.complete(lease) as done:
            done.db.add(Task(type="side.effect", payload={}))

    with sessions() as db:
        assert db.execute(select(Task.type)).scalars().all() == ["meal.analyze"]
        row = _row(db, put.id)
        assert row.status is TaskStatus.PROCESSING
        assert row.lease_token == other_token


def test_complete_that_fails_to_commit_raises_and_leaves_the_lease(sessions, queue):
    """DONE 커밋 자체가 깨지면(직렬화 불가 등) 예외가 올라오고 행은 PROCESSING 그대로다.

    루프가 그 예외를 `fail` 로 넘긴다 — 다음 테스트.
    """
    put = _put(sessions)
    lease = queue.claim()

    with pytest.raises(Exception):
        with queue.complete(lease) as done:
            done.result = {"finishedAt": datetime.now(timezone.utc)}

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PROCESSING
        assert row.result is None


# ─────────────────────────── 실패 ───────────────────────────


def test_fail_returns_the_task_to_pending_with_backoff(sessions, queue):
    put = _put(sessions)
    before = datetime.now(timezone.utc)
    lease = queue.claim()

    queue.fail(lease, RuntimeError("AI 가 500 을 냈다"))

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert row.attempts == 1
        assert "RuntimeError" in row.last_error
        assert row.result is None
        assert row.lease_token is None
        assert row.lease_expires_at is None
        # 첫 실패 → 30초 뒤. 곧바로 다시 집으면 재시도가 순식간에 소진된다.
        assert before + timedelta(seconds=20) < row.next_run_at < before + timedelta(seconds=40)


def test_second_failure_backs_off_longer(sessions, queue):
    put = _put(sessions, attempts=1)
    before = datetime.now(timezone.utc)
    lease = queue.claim()

    queue.fail(lease, RuntimeError("또 실패"))

    with sessions() as db:
        next_run_at = _row(db, put.id).next_run_at
        assert before + timedelta(seconds=50) < next_run_at < before + timedelta(seconds=70)


def test_commit_failure_is_recorded_through_fail(sessions, queue):
    put = _put(sessions)
    lease = queue.claim()

    with pytest.raises(Exception) as caught:
        with queue.complete(lease) as done:
            done.result = {"finishedAt": datetime.now(timezone.utc)}
    queue.fail(lease, caught.value)

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert "TypeError" in row.last_error


def test_task_is_quarantined_after_max_attempts(sessions, queue):
    """3번째로 집힌 시도가 실패하면 FAILED 로 옮기고 더 집지 않는다 — DLQ 자리다."""
    put = _put(sessions, attempts=2)
    lease = queue.claim()

    queue.fail(lease, RuntimeError("세 번째 실패"))

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.FAILED
        assert row.attempts == 3
    assert queue.claim() is None


def test_non_retryable_failure_is_quarantined_on_the_first_attempt(sessions, queue):
    from app.infra.queue import NonRetryableError

    put = _put(sessions)
    lease = queue.claim()

    queue.fail(lease, NonRetryableError("AI 가 422 를 냈다"))

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.FAILED
        assert row.attempts == 1
        assert "NonRetryableError" in row.last_error
    assert queue.claim() is None


def test_late_failure_does_not_revive_a_done_task(sessions, queue):
    """이미 끝난 작업에 뒤늦은 실패 기록이 와도 건드리지 않는다 — 토큰 가드."""
    put = _put(sessions)
    lease = queue.claim()
    with queue.complete(lease) as done:
        done.result = {"ok": True}

    queue.fail(lease, RuntimeError("늦게 도착한 실패 기록"))

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.DONE
        assert row.last_error is None


def test_failure_message_drops_the_sql_dump_and_is_truncated(sessions, queue):
    """IntegrityError 의 str() 은 원본 파라미터(음식명 등)를 붙인다 — 규칙 6."""
    put = _put(sessions)
    lease = queue.claim()

    queue.fail(lease, RuntimeError("x" * 600 + "\n[SQL: INSERT 비밀 음식명]"))

    with sessions() as db:
        row = _row(db, put.id)
        assert "비밀" not in row.last_error
        assert len(row.last_error) <= 500


# ─────────────────────────── 반납 ───────────────────────────


def test_release_does_not_consume_an_attempt(sessions, queue):
    """종료 신호로 놓는 건 실패가 아니다. 배포할 때마다 한 번씩 깎이면 멀쩡한 작업이 격리된다."""
    put = _put(sessions)
    lease = queue.claim()

    queue.release(lease)

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert row.attempts == 0
        assert row.last_error is None
        assert row.lease_token is None
        assert row.next_run_at <= datetime.now(timezone.utc) + timedelta(seconds=1)


# ─────────────────────────── 회수 ───────────────────────────


def test_expired_lease_is_reclaimed_by_the_next_claim(sessions, queue):
    """워커가 죽어 lease 가 지나면 다음 claim 이 PENDING 으로 되돌린다(backoff 적용)."""
    from app.infra.queue import LeaseLostError

    put = _put(sessions)
    lease = queue.claim()
    _expire(sessions, put.id)
    before = datetime.now(timezone.utc)

    assert queue.claim() is None  # 회수된 작업은 backoff 뒤에 다시 집힌다

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert row.attempts == 1
        assert row.last_error.startswith("LeaseExpired")
        assert row.lease_token is None
        assert before + timedelta(seconds=20) < row.next_run_at < before + timedelta(seconds=40)

    # 뒤늦게 깨어난 원래 워커의 완료는 반영되지 않는다.
    with pytest.raises(LeaseLostError):
        with queue.complete(lease):
            pass


def test_expired_lease_at_max_attempts_is_quarantined(sessions, queue):
    """워커를 죽이는 작업도 상한에 걸린다 — attempts 를 집을 때 세는 이유다."""
    put = _put(sessions, attempts=2)
    queue.claim()
    _expire(sessions, put.id)

    assert queue.claim() is None

    with sessions() as db:
        assert _row(db, put.id).status is TaskStatus.FAILED


def test_live_lease_is_not_reclaimed(sessions, queue):
    put = _put(sessions)
    lease = queue.claim()

    assert queue.claim() is None

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PROCESSING
        assert row.lease_token == lease.token
