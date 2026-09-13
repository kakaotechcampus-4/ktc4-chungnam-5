"""작업 타입 → 핸들러.

## 작업을 하나 붙이려면

1. `jobs/` 에 파일을 만들고 `run(task, ai)` 를 둔다
2. 아래 `_HANDLERS` 에 한 줄 더한다
3. `_NOT_IMPLEMENTED` 에서 그 타입을 지운다

이 파일만 공유된다. 추가되는 건 import 한 줄과 매핑 한 줄뿐이라, 두 사람이 동시에
건드려도 "둘 다 유지" 로 끝난다.

## 피드백 셋을 한 타입으로 묶지 않는다

AI 쪽은 끼니와 하루를 같은 `/short-feedback` 으로 받는다 — AI 가 하는 일이 같기 때문이다.
하지만 Worker 는 다르다. 모으는 데이터도 쓰는 테이블도 셋이 전부 다르다.

묶으면 `type` 이 정보를 거의 담지 못하고 진짜 구분자가 본문 안에 숨는다. DLQ 분류도
쓸모가 없어진다 — "feedback 10건 실패" 보다 "feedback.long 10건 실패" 가 훨씬 낫다.
"""

from __future__ import annotations

from collections.abc import Callable

from app.infra.ai import AiClient
from app.infra.queue import ReceivedTask
from app.worker.jobs import analyze_meal

_HANDLERS: dict[str, Callable[[ReceivedTask, AiClient], None]] = {
    "meal.analyze": analyze_meal.run,
}

# 계약은 정해졌지만 아직 구현이 없는 것들. 알 수 없는 타입과 구분해서 알려 준다.
_NOT_IMPLEMENTED = {
    "meal.evaluate": "Q/Q/S 평가 (Rule Engine → qqs_evaluations. AI 를 부르지 않는다)",
    "feedback.meal": "끼니 피드백 (/short-feedback scope=MEAL → meal_feedbacks)",
    "feedback.daily": "일일 피드백 (/short-feedback scope=DAILY → daily_feedbacks)",
    "feedback.long": "장기 피드백 (/long-feedback → long_term_feedbacks)",
}


def handle(task: ReceivedTask, ai: AiClient) -> None:
    task_type = task.body.get("type")

    handler = _HANDLERS.get(task_type)
    if handler is not None:
        return handler(task, ai)

    if task_type in _NOT_IMPLEMENTED:
        raise NotImplementedError(f"{_NOT_IMPLEMENTED[task_type]} 미구현")

    raise ValueError(f"알 수 없는 작업 타입: {task_type!r}")
