"""작업 하나의 모양 — load / call_ai / apply.

AI 호출을 트랜잭션 밖으로 빼려고 핸들러를 세 단계로 쪼갠다. 단계마다 받는 것이 다르다:

    load(db, task)                    읽기 세션. 끝나면 큐가 **롤백**한다. AI 가 필요 없으면 Skip
    call_ai(ctx, ai)                  세션이 없다. AI 를 부를 필요가 없으면 None
    apply(db, task, ctx, resp)        쓰기 세션. 끝나면 큐가 DONE 과 함께 커밋한다
    on_ai_error(db, task, ctx, exc)   (선택) call_ai 가 터졌을 때 도메인에 실패를 남겨야 하면

`call_ai` 가 `db` 를 받지 않는 게 핵심이다 — AI 를 기다리는 45초 동안 커넥션을 쥘 방법이
없다. 그래서 `load` 가 넘기는 ctx 는 ORM 객체가 아니라 순수 데이터여야 한다. 세션이 닫히면
ORM 객체는 쓸 수 없다.

쓰기는 `apply` · `on_ai_error` 에서만 한다. 거기서도 커밋은 큐가 한다 — 이 세션으로
커밋하는 `services/` 함수를 부르면 DONE 과 도메인 변경이 갈라진다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.infra.ai import AiClient
from app.infra.queue import ClaimedTask


@dataclass(frozen=True)
class Skip:
    """AI 를 부르지 않고 끝낸다. `result` 는 `task_queue.result` 에 남는다."""

    result: dict[str, Any] | None = None


@dataclass(frozen=True)
class Job:
    load: Callable[[Session, ClaimedTask], Any]
    call_ai: Callable[[Any, AiClient], Any]
    apply: Callable[[Session, ClaimedTask, Any, Any], dict[str, Any] | None]
    on_ai_error: Callable[[Session, ClaimedTask, Any, Exception], dict[str, Any] | None] | None = None

    def run_inline(self, db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None:
        """세 단계를 **세션 하나로** 이어 돈다. 커밋하지 않는다.

        테스트용이다 — 워커 루프(`worker/loop.py::process_one`)는 단계마다 세션을 따로 연다.
        """
        ctx = self.load(db, task)
        if isinstance(ctx, Skip):
            return ctx.result
        try:
            response = self.call_ai(ctx, ai)
        except Exception as exc:
            if self.on_ai_error is None:
                raise
            return self.on_ai_error(db, task, ctx, exc)
        return self.apply(db, task, ctx, response)
