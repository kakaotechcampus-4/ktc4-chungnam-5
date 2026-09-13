"""워커 루프 검증.

이 루프에서 틀리기 쉬운 건 하나다 — 실패한 작업을 지워 버리는 것.
지우면 재시도도 DLQ 도 일어나지 않고, 작업이 조용히 사라진다.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.infra.queue import ReceivedTask
from app.worker.dispatch import handle
from app.worker.loop import run


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

        from app.worker import loop

        loop.request_stop()
        return []

    def delete(self, receipt: str) -> None:
        self.deleted.append(receipt)


class FakeAi:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self._response = response or {"mealId": "m1", "items": [], "safetyStatus": "SAFE"}
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def analyze_meal(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(payload)
        if self._error:
            raise self._error
        return self._response

    def short_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def long_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _reset_running():
    from app.worker import loop

    loop._running = True
    yield
    loop._running = True


def _analyze_body(meal_id: str = "m1") -> dict[str, Any]:
    return {
        "type": "meal.analyze",
        "mealId": meal_id,
        "mealType": "LUNCH",
        "eatenAt": "2026-08-21T12:40:00+09:00",
        "stage": "MAINTENANCE",
        "rawText": "김밥 한 줄",
    }


def _task(body: dict[str, Any], receipt: str = "r1", receive_count: int = 1) -> ReceivedTask:
    return ReceivedTask(body=body, receipt=receipt, receive_count=receive_count)


# ─────────────────────────── 삭제 시점 ───────────────────────────


def test_failed_task_is_not_deleted(monkeypatch):
    """처리가 터지면 지우지 않는다 — visibility timeout 뒤 재배달돼야 한다."""

    def boom(task, ai):
        raise RuntimeError("AI 가 500 을 냈다")

    monkeypatch.setattr("app.worker.loop.handle", boom)
    queue = FakeQueue([_task(_analyze_body())])

    run(queue, FakeAi())

    assert queue.deleted == []


def test_successful_task_is_deleted(monkeypatch):
    monkeypatch.setattr("app.worker.loop.handle", lambda task, ai: None)
    queue = FakeQueue([_task(_analyze_body(), receipt="abc")])

    run(queue, FakeAi())

    assert queue.deleted == ["abc"]


def test_one_failure_does_not_stop_the_batch(monkeypatch):
    """한 건이 터져도 나머지는 처리된다."""

    def flaky(task: ReceivedTask, ai: Any) -> None:
        if task.receipt == "bad":
            raise RuntimeError("처리 실패")

    monkeypatch.setattr("app.worker.loop.handle", flaky)
    queue = FakeQueue(
        [
            _task(_analyze_body(), receipt="bad"),
            _task(_analyze_body(), receipt="good"),
        ]
    )

    run(queue, FakeAi())

    assert queue.deleted == ["good"]


# ─────────────────────────── 작업 분기 ───────────────────────────


def test_unknown_task_type_raises():
    with pytest.raises(ValueError, match="알 수 없는 작업 타입"):
        handle(_task({"type": "nope"}), FakeAi())


@pytest.mark.parametrize(
    "task_type",
    ["meal.evaluate", "feedback.meal", "feedback.daily", "feedback.long"],
)
def test_remaining_task_types_are_not_implemented_yet(task_type):
    """파이프라인이 붙으면 이 테스트를 지운다."""
    with pytest.raises(NotImplementedError):
        handle(_task({"type": task_type}), FakeAi())


def test_feedback_types_are_not_collapsed_into_one():
    """피드백 셋을 한 타입으로 묶지 않는다 — 모으는 데이터도 쓰는 테이블도 다르다."""
    with pytest.raises(ValueError, match="알 수 없는 작업 타입"):
        handle(_task({"type": "feedback.generate"}), FakeAi())
