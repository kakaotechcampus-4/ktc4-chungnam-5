"""워커 루프의 삭제 시점 검증.

이 루프에서 틀리기 쉬운 건 하나다 — 실패한 작업을 지워 버리는 것.
지우면 재시도도 DLQ 도 일어나지 않고, 작업이 조용히 사라진다.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.infra.queue import ReceivedTask
from app.worker_main import handle, run


class FakeQueue:
    """한 배치만 돌려주고 그 뒤로는 빈 결과를 주는 가짜 큐."""

    def __init__(self, tasks: list[ReceivedTask]) -> None:
        self._batches = [tasks]
        self.deleted: list[str] = []
        self.sent: list[dict[str, Any]] = []

    def send(self, body: dict[str, Any]) -> None:
        self.sent.append(body)

    def receive(self, max_count: int = 1, wait_seconds: int = 5) -> list[ReceivedTask]:
        if self._batches:
            return self._batches.pop(0)
        # 루프를 멈춘다
        import app.worker_main as worker_main

        worker_main._running = False
        return []

    def delete(self, receipt: str) -> None:
        self.deleted.append(receipt)


@pytest.fixture(autouse=True)
def _reset_running():
    import app.worker_main as worker_main

    worker_main._running = True
    yield
    worker_main._running = True


def _task(body: dict[str, Any], receipt: str = "r1", receive_count: int = 1) -> ReceivedTask:
    return ReceivedTask(body=body, receipt=receipt, receive_count=receive_count)


def test_failed_task_is_not_deleted(monkeypatch):
    """처리가 터지면 지우지 않는다 — visibility timeout 뒤 재배달돼야 한다."""
    queue = FakeQueue([_task({"type": "meal.analyze", "mealId": "m1"})])

    run(queue)

    assert queue.deleted == []


def test_successful_task_is_deleted(monkeypatch):
    monkeypatch.setattr("app.worker_main.handle", lambda task: None)
    queue = FakeQueue([_task({"type": "meal.analyze", "mealId": "m1"}, receipt="abc")])

    run(queue)

    assert queue.deleted == ["abc"]


def test_one_failure_does_not_stop_the_batch(monkeypatch):
    """한 건이 터져도 나머지는 처리된다."""

    def flaky(task: ReceivedTask) -> None:
        if task.receipt == "bad":
            raise RuntimeError("처리 실패")

    monkeypatch.setattr("app.worker_main.handle", flaky)
    queue = FakeQueue(
        [
            _task({"type": "meal.analyze"}, receipt="bad"),
            _task({"type": "meal.analyze"}, receipt="good"),
        ]
    )

    run(queue)

    assert queue.deleted == ["good"]


def test_unknown_task_type_raises():
    with pytest.raises(ValueError, match="알 수 없는 작업 타입"):
        handle(_task({"type": "nope"}))


@pytest.mark.parametrize(
    "task_type",
    ["meal.analyze", "meal.evaluate", "feedback.generate"],
)
def test_known_task_types_are_not_implemented_yet(task_type):
    """파이프라인이 붙으면 이 테스트를 지운다."""
    with pytest.raises(NotImplementedError):
        handle(_task({"type": task_type}))
