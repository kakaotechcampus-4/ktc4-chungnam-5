"""워커 루프 검증.

이 루프에서 틀리기 쉬운 건 둘이다 — 실패한 작업을 완료로 커밋해 버리는 것(작업이 조용히
사라진다), 그리고 AI 를 기다리는 동안 DB 커넥션을 쥐는 것(이 구조를 만든 이유가 없어진다).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import text

from app.infra.queue import ClaimedTask, Completion, Lease, LeaseLostError
from app.worker.dispatch import get_job
from app.worker.job import Job, Skip
from app.worker.loop import process_one, run


class FakeQueue:
    """작업 몇 개를 차례로 빌려 주고 그 뒤로는 None 을 주는 가짜 큐.

    `complete` 블록이 정상으로 끝나면 done 에 담는다. 실패·반납은 호출된 그대로 기록한다.
    """

    def __init__(self, tasks: list[ClaimedTask], *, lose_lease: bool = False) -> None:
        self._tasks = list(tasks)
        self._lose_lease = lose_lease
        self.done: list[tuple[uuid.UUID, dict[str, Any] | None]] = []
        self.failed: list[tuple[uuid.UUID, str]] = []
        self.released: list[uuid.UUID] = []

    def claim(self) -> Lease | None:
        if not self._tasks:
            from app.worker import loop

            loop.request_stop()
            return None
        return Lease(task=self._tasks.pop(0), token=uuid.uuid4())

    @contextmanager
    def read(self):
        yield None

    @contextmanager
    def complete(self, lease: Lease):
        completion = Completion(db=None)
        yield completion
        if self._lose_lease:
            raise LeaseLostError(str(lease.task.id))
        self.done.append((lease.task.id, completion.result))

    def fail(self, lease: Lease, exc: BaseException) -> None:
        self.failed.append((lease.task.id, type(exc).__name__))

    def release(self, lease: Lease) -> None:
        self.released.append(lease.task.id)


class FakeAi:
    pass


@pytest.fixture(autouse=True)
def _reset_running():
    from app.worker import loop

    loop._running = True
    yield
    loop._running = True


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """빈 큐일 때의 폴링 대기를 없앤다. 테스트가 1초씩 쉴 이유가 없다."""
    monkeypatch.setattr("app.worker.loop.time.sleep", lambda _seconds: None)


def _task(task_type: str = "meal.analyze", attempts: int = 0) -> ClaimedTask:
    return ClaimedTask(id=uuid.uuid4(), type=task_type, payload={"mealId": "m1"}, attempts=attempts)


def _use(monkeypatch, job: Job) -> None:
    monkeypatch.setattr("app.worker.loop.get_job", lambda _task_type: job)


def _boom(*_args):
    raise RuntimeError("AI 가 500 을 냈다")


# ─────────────────────────── 단계와 커밋 시점 ───────────────────────────


def test_success_completes_with_the_apply_result(monkeypatch):
    _use(
        monkeypatch,
        Job(
            load=lambda db, task: {"n": 1},
            call_ai=lambda ctx, ai: {"echo": ctx["n"]},
            apply=lambda db, task, ctx, resp: {"items": resp["echo"]},
        ),
    )
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == [(task.id, {"items": 1})]
    assert queue.failed == []


def test_skip_completes_without_calling_ai(monkeypatch):
    _use(monkeypatch, Job(load=lambda db, task: Skip({"skipped": "DELETED"}), call_ai=_boom, apply=_boom))
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == [(task.id, {"skipped": "DELETED"})]


def test_ai_failure_without_on_ai_error_is_recorded_as_failure(monkeypatch):
    """AI 가 터지면 완료로 커밋하지 않는다 — 실패로 넘겨 재시도에 맡긴다."""
    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=_boom, apply=_boom))
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == []
    assert queue.failed == [(task.id, "RuntimeError")]


def test_ai_failure_with_on_ai_error_completes_with_its_result(monkeypatch):
    _use(
        monkeypatch,
        Job(
            load=lambda db, task: {},
            call_ai=_boom,
            apply=_boom,
            on_ai_error=lambda db, task, ctx, exc: {"status": "FAILED"},
        ),
    )
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == [(task.id, {"status": "FAILED"})]


def test_on_ai_error_that_reraises_is_recorded_as_failure(monkeypatch):
    def reraise(db, task, ctx, exc):
        raise exc

    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=_boom, apply=_boom, on_ai_error=reraise))
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == []
    assert queue.failed == [(task.id, "RuntimeError")]


def test_apply_failure_is_recorded_as_failure(monkeypatch):
    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=lambda ctx, ai: {}, apply=_boom))
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == []
    assert queue.failed == [(task.id, "RuntimeError")]


def test_lost_lease_is_not_recorded_as_failure(monkeypatch):
    """lease 를 잃었으면 그 작업은 이제 다른 워커 몫이다. 실패를 기록하면 남의 시도를 깎는다."""
    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=lambda ctx, ai: {}, apply=lambda *a: None))
    queue = FakeQueue([_task()], lose_lease=True)

    run(queue, FakeAi())

    assert queue.done == []
    assert queue.failed == []


def test_keyboard_interrupt_releases_the_lease(monkeypatch):
    def interrupt(ctx, ai):
        raise KeyboardInterrupt()

    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=interrupt, apply=_boom))
    task = _task()
    queue = FakeQueue([task])

    with pytest.raises(KeyboardInterrupt):
        run(queue, FakeAi())

    assert queue.released == [task.id]
    assert queue.failed == []


def test_one_failure_does_not_stop_the_worker(monkeypatch):
    bad, good = _task(), _task()

    def flaky(ctx, ai):
        if ctx["id"] == bad.id:
            raise RuntimeError("처리 실패")
        return {}

    _use(monkeypatch, Job(load=lambda db, task: {"id": task.id}, call_ai=flaky, apply=lambda *a: None))
    queue = FakeQueue([bad, good])

    run(queue, FakeAi())

    assert queue.failed == [(bad.id, "RuntimeError")]
    assert [task_id for task_id, _result in queue.done] == [good.id]


def test_claim_error_does_not_stop_the_worker(monkeypatch):
    """claim 자체가 터져도(DB 순단) 쉬었다가 다시 돈다."""
    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=lambda ctx, ai: {}, apply=lambda *a: None))
    task = _task()
    queue = FakeQueue([task])
    real_claim = queue.claim
    calls = []

    def flaky_claim():
        calls.append(None)
        if len(calls) == 1:
            raise ConnectionError("DB 가 잠깐 죽었다")
        return real_claim()

    queue.claim = flaky_claim

    run(queue, FakeAi())

    assert [task_id for task_id, _result in queue.done] == [task.id]


def test_unknown_task_type_is_recorded_as_failure():
    task = _task("nope")
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.failed == [(task.id, "ValueError")]


# ─────────────────────────── 커넥션 ───────────────────────────


def test_no_db_connection_is_held_while_ai_is_called(sessions, test_engine, monkeypatch):
    """진짜 큐로 돌려, AI 를 기다리는 동안 이 워커가 쥔 커넥션이 0 인지 본다.

    이 구조를 만든 이유 그 자체다. 누가 `call_ai` 에 세션을 넘기거나 `read` 블록 안에서
    AI 를 부르게 바꾸면 여기서 걸린다.
    """
    from app.infra.queue import DbTaskQueue
    from app.models.task import Task

    seen: list[int] = []

    def call_ai(ctx, ai):
        seen.append(test_engine.pool.checkedout())
        return {"ok": True}

    # load 가 SQL 을 실행해야 세션이 커넥션을 실제로 꺼낸다 — 세션은 첫 실행에서야 풀에서 가져온다.
    _use(
        monkeypatch,
        Job(
            load=lambda db, task: {"n": db.execute(text("SELECT 1")).scalar()},
            call_ai=call_ai,
            apply=lambda db, task, ctx, resp: resp,
        ),
    )
    with sessions() as db:
        db.add(Task(type="meal.analyze", payload={"mealId": "m1"}))
        db.commit()
    queue = DbTaskQueue(sessions)

    process_one(queue, FakeAi(), queue.claim())

    assert seen == [0]


# ─────────────────────────── 작업 분기 ───────────────────────────


def test_unknown_task_type_raises():
    with pytest.raises(ValueError, match="알 수 없는 작업 타입"):
        get_job("nope")


def test_feedback_types_are_not_collapsed_into_one():
    """피드백 셋을 한 타입으로 묶지 않는다 — 모으는 데이터도 쓰는 테이블도 다르다."""
    with pytest.raises(ValueError, match="알 수 없는 작업 타입"):
        get_job("feedback.generate")


# ─────────────────────────── 기동 검사 ───────────────────────────


def test_lease_must_outlast_the_ai_timeout():
    """lease 가 AI 타임아웃보다 짧으면 정상 작업이 회수돼 두 번 돈다. 기동을 거부한다."""
    from app.infra.ai import AiSettings
    from app.infra.queue import QueueSettings
    from app.worker_main import check_lease

    with pytest.raises(ValueError, match="QUEUE_LEASE_SEC"):
        check_lease(QueueSettings(QUEUE_LEASE_SEC=45), AiSettings(AI_TIMEOUT_SEC=45))

    check_lease(QueueSettings(QUEUE_LEASE_SEC=120), AiSettings(AI_TIMEOUT_SEC=45))
