r"""큐 ↔ ai-stub 왕복 확인.

PowerShell 창 둘로 띄운다:

    cd ai-stub
    .\.venv\Scripts\Activate.ps1
    uvicorn main:app --port 8001          # 스텁

    cd backend
    .\.venv\Scripts\Activate.ps1
    python -m scripts.smoke_queue_ai

정상 경로와 실패 경로를 한 번씩 돌린다. 실패 경로가 핵심이다 — AI 가 죽었을 때
작업이 DONE 으로 커밋되지 않고, attempts 가 오르고, 3회째에 FAILED 로 격리되는지.

큐 컨테이너는 없다. 작업은 로컬 DB 의 task_queue 테이블에 들어간다.
"""

from __future__ import annotations

import socket
import sys
import uuid
from typing import Any

from sqlalchemy import select, text, update

from app.db.session import SessionLocal
from app.infra.ai import AiSettings, build_ai_client
from app.infra.queue import DbTaskQueue, QueueSettings, enqueue
from app.models.enums import TaskStatus
from app.models.task import Task

MARKER_PREFIX = "smoke-"


def _check(label: str, host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"  {label} OK ({host}:{port})")
    except OSError:
        sys.exit(f"  {label} 에 붙지 못했다 ({host}:{port}). 띄우고 다시 실행할 것.")


def _payload(marker: str) -> dict[str, Any]:
    return {
        "mealId": marker,
        "mealType": "LUNCH",
        "eatenAt": "2026-08-21T12:40:00+09:00",
        "stage": "MAINTENANCE",
        "rawText": "김밥 한 줄",
    }


def _put(marker: str) -> None:
    with SessionLocal() as db:
        enqueue(db, "meal.analyze", _payload(marker))
        db.commit()


def _row(marker: str) -> Task:
    with SessionLocal() as db:
        return db.execute(
            select(Task).where(Task.payload["mealId"].astext == marker)
        ).scalar_one()


class _WrongTaskError(RuntimeError):
    """이 스크립트가 넣지 않은 작업을 집었다는 신호. AI 실패 시나리오와 구분하기 위한
    전용 타입이다 — 아래 `except Exception` 이 이것까지 삼키면 안 된다."""


def _run_once(queue: DbTaskQueue, ai) -> None:
    """작업 하나를 집어 AI 를 부른다. 스텁이 500 을 내면 예외가 그대로 올라온다.

    `claim()` 은 마커와 무관하게 가장 오래된 PENDING 행을 집는다. 로컬 개발 DB 에
    이 스크립트가 넣지 않은 진짜 작업이 있으면 그걸 집어 DONE 으로 커밋해 버릴 수
    있다 — 그래서 집은 작업의 mealId 가 이 스크립트의 마커(`MARKER_PREFIX`)로
    시작하지 않으면 예외를 던져 롤백시킨다(도메인 변경은 없으니 그 작업 자체는
    손대지 않는다). 단, `claim()` 은 이 예외도 똑같이 실패로 기록한다 — 즉
    **남의 작업의 attempts 를 1 올린다.** 계속 돌면 계속 깎이므로, 아래에서
    이 경우만 곧바로 프로세스를 멈춘다.
    """
    try:
        with queue.claim() as claim:
            if claim is None:
                print("    집을 작업이 없다")
                return
            meal_id = claim.task.payload.get("mealId", "")
            if not meal_id.startswith(MARKER_PREFIX):
                raise _WrongTaskError(f"mealId={meal_id!r}")
            claim.result = ai.analyze_meal(claim.task.payload)
            print(f"    시도 {claim.task.attempts + 1}회째 — 성공")
    except _WrongTaskError as exc:
        sys.exit(
            f"    로컬 DB 의 다른 작업을 집었다({exc}). 그 작업의 attempts 가 이미 "
            "1 올라갔다 — 사람이 확인할 것. 스모크를 멈춘다."
        )
    except Exception as exc:
        print(f"    실패: {type(exc).__name__}")


def _clear_backoff(marker: str) -> None:
    """backoff 를 0 으로 되돌려 재시도를 앞당긴다.

    실제 운영에서는 이 줄이 없고 30초·60초 뒤에 다시 집힌다.
    """
    with SessionLocal() as db:
        db.execute(
            update(Task)
            .where(Task.payload["mealId"].astext == marker)
            .values(next_run_at=text("now()"))
        )
        db.commit()


def _purge() -> None:
    with SessionLocal() as db:
        db.execute(text("DELETE FROM task_queue WHERE payload->>'mealId' LIKE :p"), {"p": f"{MARKER_PREFIX}%"})
        db.commit()


def main() -> None:
    print("전제 조건")
    _check("ai-stub", "localhost", 8001)
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    print("  PostgreSQL OK")

    _purge()
    settings = QueueSettings()
    queue = DbTaskQueue(settings=settings)

    # ── 정상 경로 ────────────────────────────────────────────
    print("\n[1] 정상 경로")
    marker = f"{MARKER_PREFIX}ok-{uuid.uuid4().hex[:6]}"
    _put(marker)
    print(f"    작업 투입 mealId={marker}")

    _run_once(queue, build_ai_client(AiSettings()))
    row = _row(marker)
    assert row.status is TaskStatus.DONE, f"DONE 이 아니다: {row.status}"
    assert row.result is not None, "result 가 비어 있다"
    print("    DONE + result 기록 ✓")

    # ── 실패 경로 ────────────────────────────────────────────
    print(f"\n[2] 실패 경로 — ai-stub 이 500 을 낸다 (최대 {settings.QUEUE_MAX_ATTEMPTS}회)")
    marker = f"{MARKER_PREFIX}fail-{uuid.uuid4().hex[:6]}"
    _put(marker)
    print(f"    작업 투입 mealId={marker}")

    failing_ai = build_ai_client(AiSettings(AI_STUB_SCENARIO="ERROR_500"))
    for _ in range(settings.QUEUE_MAX_ATTEMPTS):
        _run_once(queue, failing_ai)
        row = _row(marker)
        print(f"      → status={row.status.value} attempts={row.attempts}")
        _clear_backoff(marker)

    row = _row(marker)
    assert row.status is TaskStatus.FAILED, f"격리되지 않았다: {row.status}"
    assert row.attempts == settings.QUEUE_MAX_ATTEMPTS
    print("    FAILED 로 격리 ✓")

    with queue.claim() as claim:
        assert claim is None, "격리된 작업이 다시 집혔다"
    print("    더 이상 집히지 않음 ✓")

    _purge()
    print("\n왕복 확인 완료.")


if __name__ == "__main__":
    main()
