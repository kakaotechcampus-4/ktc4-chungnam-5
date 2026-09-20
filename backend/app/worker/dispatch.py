"""작업 타입 → 핸들러.

## 작업을 하나 붙이려면

1. `jobs/` 에 파일을 만들고 `run(db, task, ai)` 를 둔다
2. 아래 `_HANDLERS` 에 한 줄 더한다
3. `_NOT_IMPLEMENTED` 에서 그 타입을 지운다

이 파일만 공유된다. 추가되는 건 import 한 줄과 매핑 한 줄뿐이라, 두 사람이 동시에
건드려도 "둘 다 유지" 로 끝난다.

## Q/Q/S 채점은 작업이 아니다

`meal.evaluate` 는 큐 타입이 아니다. Rule Engine 은 순수 함수라 0.01 초면 끝나고,
확인 화면이 점수를 **곧바로** 보여 준다 — 큐에 넣으면 그 화면에 로딩이 생긴다.
확인 API 가 동기로 채점해 응답에 담는다.

피드백도 자동으로 이어 붙이지 않는다. 사용자가 "다음 끼니 제안 보기" 를 눌렀을 때
그 API 가 `feedback.meal` 을 넣는다. 이어 붙이면 아무도 안 볼 피드백까지 AI 를 부른다.

여기 있는 건 **AI 를 부르는 작업뿐이다.**

## 피드백 셋을 한 타입으로 묶지 않는다

AI 쪽은 끼니와 하루를 같은 `/short-feedback` 으로 받는다 — AI 가 하는 일이 같기 때문이다.
하지만 Worker 는 다르다. 모으는 데이터도 쓰는 테이블도 셋이 전부 다르다.

묶으면 `type` 이 정보를 거의 담지 못하고 진짜 구분자가 본문 안에 숨는다. DLQ 분류도
쓸모가 없어진다 — "feedback 10건 실패" 보다 "feedback.long 10건 실패" 가 훨씬 낫다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.infra.ai import AiClient
from app.infra.queue import ClaimedTask

_HANDLERS: dict[str, Callable[[Session, ClaimedTask, AiClient], dict[str, Any] | None]] = {}

# 계약은 정해졌지만 아직 구현이 없는 것들. 알 수 없는 타입과 구분해서 알려 준다.
_NOT_IMPLEMENTED = {
    "meal.analyze": "사진·텍스트 → meal_items 인식 (crud/ 재작성 대기)",
    "feedback.meal": "끼니 피드백 (/short-feedback scope=MEAL → meal_feedbacks)",
    "feedback.daily": "일일 피드백 (/short-feedback scope=DAILY → daily_feedbacks)",
    "feedback.long": "장기 피드백 (/long-feedback → long_term_feedbacks)",
}


def handle(db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None:
    """작업 하나를 처리한다.

    `db` 는 이 작업을 잠그고 있는 세션이다. 핸들러가 도메인 쓰기에 그대로 써야
    작업 완료와 도메인 변경이 한 트랜잭션이 된다. 커밋은 하지 않는다 — 큐가 한다.

    **주의: 이 `db` 로 부르는 `services/`·`crud/` 함수는 절대 커밋하면 안 된다.**
    이 레포 관례상 `services/` 가 트랜잭션 경계를 정하고 커밋도 직접 하는데
    (`services/meal.py`·`services/user.py` 참고), 그런 함수를 여기서 그대로 부르면
    AI 호출이 끝나기 한참 전에 행 잠금이 풀린다 — 다른 워커가 같은 작업을 동시에
    집어 중복 처리하고, 이후 정말 실패해도 이미 커밋된 도메인 변경은 롤백되지 않는다.
    커밋하는 service 를 붙여야 하면, 커밋 없는 버전으로 쪼개 그 함수를 부른다.

    반환값은 `task_queue.result` 에 남는다. 남길 게 없으면 None.
    """
    handler = _HANDLERS.get(task.type)
    if handler is not None:
        return handler(db, task, ai)

    if task.type in _NOT_IMPLEMENTED:
        raise NotImplementedError(f"{_NOT_IMPLEMENTED[task.type]} 미구현")

    raise ValueError(f"알 수 없는 작업 타입: {task.type!r}")
