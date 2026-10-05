"""워커 기동 — 스레드 N개로 폴링 루프를 돌린다.

틀리기 쉬운 건 셋이다. 스레드가 실제로 동시에 AI 를 기다리는가, 스레드가 늘어도 작업이
정확히 한 번씩만 처리되는가, 스레드 하나가 죽었을 때 나머지가 조용히 계속 도는가.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.infra.queue import ClaimedTask, Completion, DbTaskQueue, Lease, QueueSettings
from app.models.enums import TaskStatus
from app.models.task import Task
from app.worker.job import Job
from app.worker_main import check_threads, run_threads


class LockedQueue:
    """여러 스레드가 같이 쓰는 가짜 큐. 작업을 한 번씩만 빌려 준다."""

    def __init__(self, tasks: list[ClaimedTask]) -> None:
        self._tasks = list(tasks)
        self._lock = threading.Lock()
        self.done: list[uuid.UUID] = []
        self.failed: list[tuple[uuid.UUID, str]] = []

    def claim(self) -> Lease | None:
        with self._lock:
            if not self._tasks:
                return None
            return Lease(task=self._tasks.pop(0), token=uuid.uuid4())

    @contextmanager
    def read(self):
        yield None

    @contextmanager
    def complete(self, lease: Lease):
        yield Completion(db=None)
        with self._lock:
            self.done.append(lease.task.id)

    def fail(self, lease: Lease, exc: BaseException) -> None:
        with self._lock:
            self.failed.append((lease.task.id, type(exc).__name__))

    def release(self, lease: Lease) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_running():
    from app.worker import loop

    loop._running = True
    yield
    loop._running = True


@pytest.fixture(autouse=True)
def _watchdog():
    """동시성이 깨지면 이 테스트들은 실패 대신 멈춘다(멈춤 조건에 영영 닿지 않는다).
    20초 뒤 루프를 강제로 멈춰, 매달리지 않고 단언에서 실패하게 한다."""
    from app.worker import loop

    timer = threading.Timer(20, loop.request_stop)
    timer.start()
    yield
    timer.cancel()


def _settings(threads: int) -> QueueSettings:
    # 빈 큐에서 1초씩 쉬면 테스트가 느려진다. .env 의 값이 섞이지 않게 한다.
    return QueueSettings(_env_file=None, QUEUE_WORKER_THREADS=threads, QUEUE_POLL_INTERVAL_SEC=0.01)


def _task() -> ClaimedTask:
    return ClaimedTask(id=uuid.uuid4(), type="meal.analyze", payload={}, attempts=0)


def _use(monkeypatch, job: Job) -> None:
    monkeypatch.setattr("app.worker.loop.get_job", lambda _task_type: job)


def _stop_after(count: int):
    """`count` 건을 처리하면 루프를 멈추는 apply. 스레드가 끝나야 run_threads 가 돌아온다."""
    from app.worker import loop

    lock = threading.Lock()
    applied: list[Any] = []

    def apply(db, task, ctx, resp):
        with lock:
            applied.append(task.id)
            if len(applied) == count:
                loop.request_stop()
        return None

    return apply, applied


# ─────────────────────────── 동시성 ───────────────────────────


def test_threads_wait_on_ai_at_the_same_time(monkeypatch):
    """작업 N개가 **동시에** AI 를 기다려야만 Barrier 를 넘는다. 순차라면 첫 작업이 timeout 으로 깨진다."""
    threads = 4
    barrier = threading.Barrier(threads, timeout=5)
    apply, _applied = _stop_after(threads)

    def call_ai(ctx, ai):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            # 동시에 기다리지 못했다. 남은 작업을 기다리지 말고 멈춰 단언에서 실패하게 한다.
            from app.worker import loop

            loop.request_stop()
            raise
        return {}

    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=call_ai, apply=apply))
    queue = LockedQueue([_task() for _ in range(threads)])

    assert run_threads(queue, object(), _settings(threads)) == 0

    assert queue.failed == []
    assert len(queue.done) == threads


def test_each_task_is_processed_exactly_once_with_a_real_queue(sessions, monkeypatch):
    """진짜 큐로 스레드 4개 × 작업 12개. SKIP LOCKED + lease 로 겹쳐 집히는 작업이 없어야 한다."""
    total = 12
    with sessions() as db:
        db.add_all(Task(type="meal.analyze", payload={"n": n}) for n in range(total))
        db.commit()
    apply, applied = _stop_after(total)
    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=lambda ctx, ai: {}, apply=apply))

    assert run_threads(DbTaskQueue(sessions, settings=_settings(4)), object(), _settings(4)) == 0

    assert len(applied) == total
    assert len(set(applied)) == total
    with sessions() as db:
        rows = db.execute(select(Task.status, Task.attempts)).all()
        assert [status for status, _ in rows] == [TaskStatus.DONE] * total
        assert {attempts for _, attempts in rows} == {1}


# ─────────────────────────── 멈추기 ───────────────────────────


def test_request_stop_ends_every_thread(monkeypatch):
    from app.worker import loop

    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=lambda ctx, ai: {}, apply=lambda *a: None))
    timer = threading.Timer(0.2, loop.request_stop)
    timer.start()

    assert run_threads(LockedQueue([]), object(), _settings(3)) == 0
    assert [t.name for t in threading.enumerate() if t.name.startswith("worker-")] == []


def test_a_dead_thread_stops_the_rest_and_reports_failure(monkeypatch):
    """스레드 하나만 조용히 죽으면 처리량이 줄어든 채로 아무도 모른다. 전체를 멈추고 1 을 돌려준다
    — 프로세스가 0 이 아닌 코드로 끝나 compose 가 다시 띄운다."""

    def die(ctx, ai):
        raise SystemExit("스레드가 죽었다")

    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=die, apply=lambda *a: None))

    assert run_threads(LockedQueue([_task()]), object(), _settings(3)) == 1
    assert [t.name for t in threading.enumerate() if t.name.startswith("worker-")] == []


# ─────────────────────────── 기동 검사 ───────────────────────────


@pytest.mark.parametrize("threads", [0, 16])
def test_thread_count_must_fit_the_connection_pool(threads):
    """스레드 하나가 한순간에 커넥션을 하나씩 쓴다. 풀 상한을 넘기면 스레드가 커넥션을 기다린다."""
    db_settings = SimpleNamespace(DB_POOL_SIZE=5, DB_MAX_OVERFLOW=10)

    with pytest.raises(ValueError, match="QUEUE_WORKER_THREADS"):
        check_threads(QueueSettings(_env_file=None, QUEUE_WORKER_THREADS=threads), db_settings)


@pytest.mark.parametrize("threads", [1, 15])
def test_thread_count_within_the_pool_is_accepted(threads):
    db_settings = SimpleNamespace(DB_POOL_SIZE=5, DB_MAX_OVERFLOW=10)

    check_threads(QueueSettings(_env_file=None, QUEUE_WORKER_THREADS=threads), db_settings)


def test_default_is_four_threads():
    assert QueueSettings(_env_file=None).QUEUE_WORKER_THREADS == 4
