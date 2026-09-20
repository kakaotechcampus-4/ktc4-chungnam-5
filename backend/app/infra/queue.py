"""비동기 작업 큐 — PostgreSQL 테이블.

D13 — `infra/` 는 `Protocol` 뒤에 구현을 숨긴다. 도메인은 어느 구현이 붙는지 모른다.

큐 미들웨어를 따로 두지 않는다. 작업량이 사용자 행동 하나당 하나 규모라 테이블
하나로 충분하고, 그 대신 **작업 등록이 도메인 커밋과 같은 트랜잭션**이 된다 —
"커밋이 먼저다" 라는, 사람이 지켜야 했던 순서 규칙이 사라진다.

워커는 `SELECT … FOR UPDATE SKIP LOCKED` 로 행 하나를 집고 처리하는 동안 잠금을
유지한다. 실패하면 롤백만으로 작업이 되돌아간다.

설계 배경은 docs/superpowers/specs/2026-09-20-db-table-queue-design.md 에 있다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.enums import TaskStatus
from app.models.task import Task


class QueueSettings(BaseSettings):
    """큐 설정.

    `core.Settings` 에 넣지 않고 여기 둔 건, 이 어댑터가 전역 설정을 import 하지 않게
    하려는 것이다. 쓰는 쪽이 만들어서 넘긴다.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── DB 테이블 큐 ────────────────────────────────────────
    QUEUE_POLL_INTERVAL_SEC: float = 1.0
    """빈 큐일 때 쉬는 시간. 롱 폴링 대신이다."""
    QUEUE_MAX_ATTEMPTS: int = 3
    """이 횟수만큼 실패하면 FAILED 로 격리한다."""
    QUEUE_BACKOFF_BASE_SEC: int = 30
    """재시도 지연의 기준. 30s → 60s 로 두 배씩 민다."""
    QUEUE_IDLE_TX_TIMEOUT_SEC: int = 120
    """작업 트랜잭션의 idle_in_transaction 상한. AI 타임아웃(45초)보다 넉넉히 위여야
    정상 작업을 죽이지 않는다. 멈춘 워커가 행을 영원히 붙잡는 것만 막는 안전망이다."""


def enqueue(db: Session, task_type: str, payload: dict[str, Any]) -> None:
    """작업을 넣는다. **커밋하지 않는다.**

    호출부(service)가 쓰던 세션을 그대로 받아 INSERT 만 한다. 그래서 도메인 변경과
    작업 등록이 한 트랜잭션이다 — `meals` INSERT 는 됐는데 작업은 안 들어가는(또는
    그 반대인) 상태가 애초에 만들어지지 않는다.

    세션을 인자로 받는 게 핵심이다. 여기서 자기 세션을 열어 커밋해 버리면 다시
    "커밋 순서를 사람이 지켜야 하는" 문제로 돌아간다.
    """
    db.add(Task(type=task_type, payload=payload))


logger = logging.getLogger("queue")

_ERROR_MAX_CHARS = 500


@dataclass(frozen=True)
class ClaimedTask:
    id: uuid.UUID
    type: str
    payload: dict[str, Any]
    attempts: int
    """지금까지 **실패한** 횟수. 첫 시도 때는 0 이다."""


@dataclass
class Claim:
    """집어 온 작업 하나와, 그것을 잠그고 있는 트랜잭션."""

    db: Session
    """이 작업을 잠근 세션. 핸들러가 도메인 쓰기에 **그대로 쓴다** — 작업 완료와
    도메인 변경이 한 트랜잭션이라야 실패했을 때 흔적이 남지 않는다."""
    task: ClaimedTask
    result: dict[str, Any] | None = None
    """핸들러 반환값을 담아 두면 DONE 커밋 때 `result` 컬럼에 함께 들어간다."""


class DbTaskQueue:
    """PostgreSQL 테이블 큐.

    `receive` 와 `delete` 를 나눌 수 없다. 처리하는 동안 행 잠금을 유지하는 것이
    이 큐의 재시도 메커니즘 전부이고, 잠금은 트랜잭션에 묶여 있기 때문이다.
    그래서 인터페이스가 컨텍스트 매니저다.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        *,
        settings: QueueSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or QueueSettings()

    @contextmanager
    def claim(self) -> Iterator[Claim | None]:
        """작업 하나를 집어 잠근 채로 넘긴다.

        블록이 정상으로 끝나면 DONE 으로 커밋하고, 예외가 나면 롤백한다 —
        롤백되면 행은 PENDING 그대로라 다음 폴링에 다시 집힌다. 실패 이력만
        별도 트랜잭션에 남긴다(그러지 않으면 롤백이 attempts 도 되돌린다).
        """
        db = self._session_factory()
        try:
            # 멈춘 워커가 행을 영원히 붙잡지 않게 하는 안전망. SET LOCAL 이라
            # 이 트랜잭션에만 걸리고 커밋·롤백과 함께 사라진다.
            db.execute(
                text(
                    "SET LOCAL idle_in_transaction_session_timeout = "
                    f"'{self._settings.QUEUE_IDLE_TX_TIMEOUT_SEC}s'"
                )
            )
            row = db.execute(
                select(Task)
                .where(Task.status == TaskStatus.PENDING, Task.next_run_at <= func.now())
                .order_by(Task.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()

            if row is None:
                db.rollback()
                yield None
                return

            claim = Claim(
                db=db,
                task=ClaimedTask(
                    id=row.id, type=row.type, payload=row.payload, attempts=row.attempts
                ),
            )

            try:
                yield claim
                # 완료 UPDATE 와 commit 을 이 try 안에 둬야 한다. `SessionLocal` 은
                # autoflush=False 라, 핸들러가 `claim.db` 에 쌓아 둔 도메인 객체의 flush 는
                # 여기 commit 시점에야 실제로 나간다 — FK 위반·UNIQUE 충돌은 물론
                # `result` 에 JSON 직렬화가 안 되는 값이 섞여도 여기서 터진다. try 밖에
                # 있으면 그 실패가 아래 except 를 타지 않아 attempts 가 오르지 않고,
                # next_run_at 도 과거 그대로라 다음 폴링에 즉시 다시 집혀 AI 를 또 부른다.
                db.execute(
                    update(Task)
                    .where(Task.id == claim.task.id)
                    .values(
                        status=TaskStatus.DONE,
                        result=claim.result,
                        finished_at=func.now(),
                        updated_at=func.now(),
                    )
                )
                db.commit()
            except (KeyboardInterrupt, SystemExit, GeneratorExit):
                # 작업이 실패한 게 아니라 프로세스가 내려가는 것이다. 롤백해서 잠금만 풀고
                # attempts 는 태우지 않는다 — 배포 때마다 한 번씩 까이면 멀쩡한 작업이 격리된다.
                db.rollback()
                raise
            except BaseException as exc:
                db.rollback()
                self._record_failure(claim.task, exc)
                raise
        finally:
            db.close()

    def _record_failure(self, task: ClaimedTask, exc: BaseException) -> None:
        """실패를 **별도 트랜잭션**에 남긴다.

        작업 트랜잭션은 이미 롤백됐다. 거기에 기록하면 같이 되돌아간다.

        여기서 또 터져도 원래 예외를 덮어쓰지 않는다 — 기록이 안 되면 attempts 가
        안 오를 뿐, 작업은 PENDING 으로 남아 다음에 다시 집힌다.
        """
        attempts = task.attempts + 1
        status = (
            TaskStatus.FAILED
            if attempts >= self._settings.QUEUE_MAX_ATTEMPTS
            else TaskStatus.PENDING
        )
        # 30s → 60s. 실패할 때마다 두 배로 민다.
        backoff = self._settings.QUEUE_BACKOFF_BASE_SEC * 2 ** task.attempts

        # 예외 메시지에 사용자 입력이 섞여 들어올 수 있다(규칙 6). 특히 SQLAlchemy
        # IntegrityError 의 str() 은 "[SQL: INSERT ...]\n[parameters: (...)]" 형태로
        # 원본 파라미터(음식명 등)를 통째로 붙인다 — 그 부분은 500자 안에도 쉽게 들어오므로
        # 자르기 전에 SQL 덤프 자체를 먼저 잘라내고, 남은 앞부분만 길이로 다시 자른다.
        message = f"{type(exc).__name__}: {exc}".split("\n[SQL:")[0]
        reason = message[:_ERROR_MAX_CHARS]

        try:
            with self._session_factory() as db:
                # 상태 가드가 없으면: A 가 롤백해 잠금을 풀고(행은 여전히 PENDING) →
                # B 가 같은 행을 집어 성공 처리해 DONE 커밋 → A 의 이 UPDATE 가
                # (READ COMMITTED 라 WHERE id=... 가 여전히 참이라) DONE 을 다시
                # PENDING 으로 되돌려 버린다. status=PENDING 가드로, 이미 다른
                # 워커가 끝낸 행이면 0행 매칭으로 조용히 넘어간다.
                db.execute(
                    update(Task)
                    .where(Task.id == task.id, Task.status == TaskStatus.PENDING)
                    .values(
                        attempts=attempts,
                        status=status,
                        last_error=reason,
                        next_run_at=func.now() + timedelta(seconds=backoff),
                        updated_at=func.now(),
                    )
                )
                db.commit()
        except Exception:
            logger.exception("작업 실패를 기록하지 못했다. 작업은 PENDING 으로 남는다.")


class TaskQueue(Protocol):
    """작업 큐. 워커는 이 모양만 안다."""

    def claim(self) -> AbstractContextManager[Claim | None]: ...


def build_task_queue() -> TaskQueue:
    return DbTaskQueue()
