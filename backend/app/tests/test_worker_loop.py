"""워커 루프 검증.

이 루프에서 틀리기 쉬운 건 하나다 — 실패한 작업을 완료로 커밋해 버리는 것.
그러면 재시도도 격리도 일어나지 않고 작업이 조용히 사라진다.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any

import pytest

from app.infra.queue import Claim, ClaimedTask
from app.worker.dispatch import handle
from app.worker.loop import run


class FakeQueue:
    """작업 몇 개를 차례로 내주고 그 뒤로는 None 을 주는 가짜 큐.

    진짜 `DbTaskQueue` 와 같은 자리에서 커밋·롤백을 흉내 낸다 — 블록이 정상으로
    끝나면 done 에, 예외가 나면 failed 에 담는다.
    """

    def __init__(self, tasks: list[ClaimedTask]) -> None:
        self._tasks = list(tasks)
        self.done: list[tuple[uuid.UUID, dict[str, Any] | None]] = []
        self.failed: list[uuid.UUID] = []

    @contextmanager
    def claim(self):
        if not self._tasks:
            from app.worker import loop

            loop.request_stop()
            yield None
            return

        claim = Claim(db=None, task=self._tasks.pop(0))
        try:
            yield claim
        except BaseException:
            self.failed.append(claim.task.id)
            raise
        self.done.append((claim.task.id, claim.result))


class FakeAi:
    def analyze_meal(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"mealId": payload.get("mealId"), "items": [], "safetyStatus": "SAFE"}

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


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """빈 큐일 때의 폴링 대기를 없앤다. 테스트가 1초씩 쉴 이유가 없다."""
    monkeypatch.setattr("app.worker.loop.time.sleep", lambda _seconds: None)


def _task(task_type: str = "meal.analyze", attempts: int = 0) -> ClaimedTask:
    return ClaimedTask(
        id=uuid.uuid4(),
        type=task_type,
        payload={
            "mealId": "m1",
            "mealType": "LUNCH",
            "eatenAt": "2026-08-21T12:40:00+09:00",
            "stage": "MAINTENANCE",
            "rawText": "김밥 한 줄",
        },
        attempts=attempts,
    )


# ─────────────────────────── 커밋 시점 ───────────────────────────


def test_failed_task_is_not_committed_as_done(monkeypatch):
    """처리가 터지면 완료로 커밋하지 않는다 — 롤백돼 다시 집혀야 한다."""

    def boom(db, task, ai):
        raise RuntimeError("AI 가 500 을 냈다")

    monkeypatch.setattr("app.worker.loop.handle", boom)
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == []
    assert queue.failed == [task.id]


def test_successful_task_is_committed_with_its_result(monkeypatch):
    monkeypatch.setattr("app.worker.loop.handle", lambda db, task, ai: {"items": 3})
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == [(task.id, {"items": 3})]
    assert queue.failed == []


def test_one_failure_does_not_stop_the_worker(monkeypatch):
    """한 건이 터져도 루프는 다음 작업을 계속 처리한다."""
    bad, good = _task(), _task()

    def flaky(db, task, ai):
        if task.id == bad.id:
            raise RuntimeError("처리 실패")
        return None

    monkeypatch.setattr("app.worker.loop.handle", flaky)
    queue = FakeQueue([bad, good])

    run(queue, FakeAi())

    assert queue.failed == [bad.id]
    assert [task_id for task_id, _result in queue.done] == [good.id]


# ─────────────────────────── 작업 분기 ───────────────────────────


def test_unknown_task_type_raises():
    with pytest.raises(ValueError, match="알 수 없는 작업 타입"):
        handle(None, _task("nope"), FakeAi())


@pytest.mark.parametrize(
    "task_type",
    ["meal.analyze", "feedback.meal", "feedback.daily", "feedback.long"],
)
def test_task_types_are_not_implemented_yet(task_type):
    """파이프라인이 붙으면 이 테스트를 지운다."""
    with pytest.raises(NotImplementedError):
        handle(None, _task(task_type), FakeAi())


def test_feedback_types_are_not_collapsed_into_one():
    """피드백 셋을 한 타입으로 묶지 않는다 — 모으는 데이터도 쓰는 테이블도 다르다."""
    with pytest.raises(ValueError, match="알 수 없는 작업 타입"):
        handle(None, _task("feedback.generate"), FakeAi())
