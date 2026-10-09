"""비동기 작업 큐 — PostgreSQL 테이블.

D13 — `infra/` 는 `Protocol` 뒤에 구현을 숨긴다. 도메인은 어느 구현이 붙는지 모른다.

큐 미들웨어를 따로 두지 않는다. 작업량이 사용자 행동 하나당 하나 규모라 테이블
하나로 충분하고, 그 대신 **작업 등록이 도메인 커밋과 같은 트랜잭션**이 된다 —
"커밋이 먼저다" 라는, 사람이 지켜야 했던 순서 규칙이 사라진다.

워커는 작업을 **lease 로 빌린다.** `claim` 이 `SELECT … FOR UPDATE SKIP LOCKED` 로 행
하나를 집어 PROCESSING · 토큰 · 만료 시각을 쓰고 곧바로 커밋한다 — AI 를 기다리는 동안
트랜잭션도 커넥션도 쥐지 않는다. 끝낼 때(`complete` · `fail` · `release`)는 토큰이 자기
것일 때만 행을 바꾼다. 워커가 죽어 lease 가 지나면 다음 `claim` 이 회수한다.

설계 배경은 docs/superpowers/specs/2026-10-05-queue-lease-design.md 에 있다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import ColumnElement, case, cast, func, select, update
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
    """이 횟수만큼 집혔는데 끝내지 못하면 FAILED 로 격리한다."""
    QUEUE_BACKOFF_BASE_SEC: int = 30
    """재시도 지연의 기준. 30s → 60s 로 두 배씩 민다."""
    QUEUE_LEASE_SEC: int = 120
    """lease 길이. 이 안에 끝내지 못하면 다른 워커가 회수한다. AI 타임아웃(45초)보다
    넉넉히 길어야 정상 작업을 두 번 돌리지 않는다 — 워커가 기동할 때 확인한다.
    AI_TIMEOUT_SEC 는 httpx 의 단계별 타임아웃이라 총 시간 상한이 아니어서 두 배 넘게 둔다."""
    QUEUE_WORKER_THREADS: int = 4
    """워커 프로세스 하나가 돌리는 폴링 스레드 수 = 동시에 처리하는 작업 수. 작업 시간은 거의
    AI 응답 대기라 스레드로 충분하다. LLM API 의 동시 처리량을 재고 나서 맞춘다. 스레드 하나가
    한순간에 커넥션을 하나씩만 짧게 쓰므로 DB 풀 상한을 넘을 수 없다 — 워커가 기동할 때 확인한다."""


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


class NonRetryableError(Exception):
    """다시 해도 같은 결과인 실패. 핸들러가 이걸 올리면 attempts 를 기다리지 않고
    곧바로 FAILED 로 격리한다.

    예: AI 가 4xx 로 요청을 거부했다 — 같은 요청은 몇 번을 보내도 같은 4xx 이고,
    실제 AI 에서는 시도마다 LLM 비용이 든다.
    """


class LeaseLostError(Exception):
    """lease 를 잃었다 — 만료돼 다른 워커가 회수했다.

    이 워커가 `complete` 블록에서 쓴 도메인 변경은 롤백됐다. 실패로 기록하지 않는다 —
    그 작업은 이제 다른 워커 몫이고, 기록하면 남의 시도를 깎는다.
    """


@dataclass(frozen=True)
class ClaimedTask:
    id: uuid.UUID
    type: str
    payload: dict[str, Any]
    attempts: int
    """이번 시도 **이전까지** 집힌 횟수. 첫 시도 때는 0 이다."""


@dataclass(frozen=True)
class Lease:
    """빌려 온 작업 하나. `token` 이 맞아야 완료·실패·반납이 행을 바꾼다."""

    task: ClaimedTask
    token: uuid.UUID


@dataclass
class Completion:
    """`complete` 블록이 쓰는 자리."""

    db: Session
    """도메인 쓰기에 쓰는 세션. 블록이 끝나면 큐가 DONE 과 함께 한 번에 커밋한다."""
    result: dict[str, Any] | None = None
    """담아 두면 `task_queue.result` 에 들어간다."""


def _seconds(value: Any) -> ColumnElement[Any]:
    # make_interval 의 인자는 (years, months, weeks, days, hours, mins, secs) 순이라 초만 채운다.
    return func.make_interval(0, 0, 0, 0, 0, 0, value)


class DbTaskQueue:
    """PostgreSQL 테이블 큐.

    한 작업이 트랜잭션 넷을 거친다 — `claim`(빌리기) · `read`(읽기, 롤백) ·
    `complete`(쓰기 + DONE) 또는 `fail`/`release`. 어느 것도 AI 호출을 감싸지 않는다.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        *,
        settings: QueueSettings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or QueueSettings()

    def claim(self) -> Lease | None:
        """만료된 lease 를 회수한 뒤 작업 하나를 빌린다. 빌린 사실은 곧바로 커밋한다."""
        with self._session_factory() as db:
            self._reclaim_expired(db)
            db.commit()

            row = db.execute(
                select(Task)
                .where(Task.status == TaskStatus.PENDING, Task.next_run_at <= func.now())
                # 정렬 키는 `ix_task_queue_pending` 의 컬럼 순서(next_run_at, created_at)와
                # 같아야 한다. `ORDER BY created_at` 만 쓰면 선두 컬럼이 어긋나 인덱스가
                # 정렬을 못 태우고, LIMIT 1 이전에 조건에 맞는 PENDING 을 전부 읽어
                # Sort 를 돌린다 — 백로그가 쌓인 순간(워커 복구, 대량 재시도) 폴링마다
                # 전체 정렬이 된다. next_run_at 우선은 재시도 backoff 순서와도 맞다.
                .order_by(Task.next_run_at, Task.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()

            if row is None:
                db.rollback()
                return None

            # 아래 UPDATE 가 identity map 의 객체를 고칠 수 있어 쓰기 전에 값을 옮겨 둔다.
            task = ClaimedTask(id=row.id, type=row.type, payload=row.payload, attempts=row.attempts)
            token = uuid.uuid4()
            db.execute(
                update(Task)
                .where(Task.id == task.id)
                .values(
                    status=TaskStatus.PROCESSING,
                    # 집을 때 센다. 워커를 죽이는 작업(OOM · SIGKILL)은 실패를 기록할 기회가
                    # 없어서, 실패할 때만 세면 lease 만료 회수로 끝없이 되살아난다.
                    attempts=Task.attempts + 1,
                    lease_token=token,
                    lease_expires_at=func.now() + _seconds(self._settings.QUEUE_LEASE_SEC),
                    updated_at=func.now(),
                )
            )
            db.commit()
            return Lease(task=task, token=token)

    @contextmanager
    def read(self) -> Iterator[Session]:
        """`load` 단계용 세션. 블록이 끝나면 **항상 롤백**한다 — 읽기만 하라는 뜻을 구조로 강제한다."""
        db = self._session_factory()
        try:
            yield db
        finally:
            db.rollback()
            db.close()

    @contextmanager
    def complete(self, lease: Lease) -> Iterator[Completion]:
        """블록의 도메인 쓰기와 DONE 을 한 트랜잭션으로 커밋한다.

        lease 가 아직 내 것일 때만 커밋한다. 아니면 `LeaseLostError` — 도메인 쓰기까지
        롤백된다. 블록이나 커밋이 터지면 롤백하고 그대로 올린다 — 실패 기록(`fail`)은
        호출부(워커 루프)가 한다.
        """
        db = self._session_factory()
        try:
            completion = Completion(db=db)
            yield completion
            # DONE UPDATE 와 commit 을 블록 뒤 같은 try 안에 둔다. `SessionLocal` 은
            # autoflush=False 라 블록에서 쌓은 도메인 객체의 flush 가 commit 에서야 나간다 —
            # FK · UNIQUE 위반, `result` 의 JSON 직렬화 실패도 여기서 터진다.
            done = db.execute(
                update(Task)
                .where(*self._owned(lease))
                .values(
                    status=TaskStatus.DONE,
                    result=completion.result,
                    finished_at=func.now(),
                    updated_at=func.now(),
                    lease_token=None,
                    lease_expires_at=None,
                )
            )
            if done.rowcount != 1:
                raise LeaseLostError(f"task {lease.task.id}")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def fail(self, lease: Lease, exc: BaseException) -> None:
        """실패를 기록하고 lease 를 놓는다. 재시도할지 격리할지 여기서 정한다.

        여기서 또 터져도 원래 예외를 덮지 않는다 — 기록이 안 되면 작업은 PROCESSING 으로
        남았다가 lease 가 지나면 회수된다. 조용히 사라지지 않는다.
        """
        status: Any = self._retry_or_quarantine()
        if isinstance(exc, NonRetryableError):
            # 재시도해도 같은 실패다. 상한까지 태우지 않고 바로 격리한다.
            status = TaskStatus.FAILED

        # 예외 메시지에 사용자 입력이 섞여 들어올 수 있다(규칙 6). 특히 SQLAlchemy
        # IntegrityError 의 str() 은 "[SQL: INSERT ...]\n[parameters: (...)]" 형태로
        # 원본 파라미터(음식명 등)를 통째로 붙인다 — 그 부분은 500자 안에도 쉽게 들어오므로
        # 자르기 전에 SQL 덤프 자체를 먼저 잘라내고, 남은 앞부분만 길이로 다시 자른다.
        message = f"{type(exc).__name__}: {exc}".split("\n[SQL:")[0]
        reason = message[:_ERROR_MAX_CHARS]

        try:
            with self._session_factory() as db:
                db.execute(
                    update(Task)
                    .where(*self._owned(lease))
                    .values(
                        status=status,
                        last_error=reason,
                        next_run_at=func.now() + self._backoff(),
                        lease_token=None,
                        lease_expires_at=None,
                        updated_at=func.now(),
                    )
                )
                db.commit()
        except Exception:
            logger.exception("작업 실패를 기록하지 못했다. lease 가 지나면 회수된다.")

    def release(self, lease: Lease) -> None:
        """종료 신호로 처리를 놓는다. 실패가 아니므로 이번 시도를 세지 않는다.

        배포할 때마다 한 번씩 깎이면 멀쩡한 작업이 QUEUE_MAX_ATTEMPTS 만에 격리된다.
        """
        try:
            with self._session_factory() as db:
                db.execute(
                    update(Task)
                    .where(*self._owned(lease))
                    .values(
                        status=TaskStatus.PENDING,
                        attempts=Task.attempts - 1,
                        next_run_at=func.now(),
                        lease_token=None,
                        lease_expires_at=None,
                        updated_at=func.now(),
                    )
                )
                db.commit()
        except Exception:
            logger.exception("작업을 반납하지 못했다. lease 가 지나면 회수된다.")

    def _reclaim_expired(self, db: Session) -> None:
        """lease 가 지난 PROCESSING 을 되돌린다. 워커가 죽었거나 lease 안에 끝내지 못했다.

        시도는 claim 때 이미 셌으므로 attempts 는 그대로다. 여러 워커가 동시에 돌려도 행
        잠금이 직렬화하고, READ COMMITTED 의 재평가로 뒤에 온 쪽은 0행이다.
        """
        db.execute(
            update(Task)
            .where(Task.status == TaskStatus.PROCESSING, Task.lease_expires_at < func.now())
            .values(
                status=self._retry_or_quarantine(),
                last_error="LeaseExpired: lease 안에 끝내지 못했다(워커 종료 또는 지연)",
                next_run_at=func.now() + self._backoff(),
                lease_token=None,
                lease_expires_at=None,
                updated_at=func.now(),
            )
        )

    def _retry_or_quarantine(self) -> ColumnElement[Any]:
        """상한에 닿았으면 FAILED, 아니면 PENDING.

        status 컬럼은 native ENUM 이다. CASE 의 가지가 둘 다 타입 없는 리터럴이면
        Postgres 가 CASE 전체를 text 로 해석해 "column is of type task_status but
        expression is of type text" 로 대입이 깨진다. 결과 타입을 못 박는다.
        """
        return cast(
            case(
                (Task.attempts >= self._settings.QUEUE_MAX_ATTEMPTS, TaskStatus.FAILED.value),
                else_=TaskStatus.PENDING.value,
            ),
            Task.__table__.c.status.type,
        )

    def _backoff(self) -> ColumnElement[Any]:
        """30s → 60s. attempts 는 claim 때 이미 1 올라 있으므로 첫 실패가 2^0 이다."""
        return _seconds(self._settings.QUEUE_BACKOFF_BASE_SEC * func.pow(2, Task.attempts - 1))

    @staticmethod
    def _owned(lease: Lease) -> tuple[ColumnElement[bool], ...]:
        """이 lease 가 아직 유효한 행. 회수됐거나 이미 끝난 행이면 0행이 된다."""
        return (
            Task.id == lease.task.id,
            Task.status == TaskStatus.PROCESSING,
            Task.lease_token == lease.token,
        )


class TaskQueue(Protocol):
    """작업 큐. 워커는 이 모양만 안다."""

    def claim(self) -> Lease | None: ...

    def read(self) -> AbstractContextManager[Session]: ...

    def complete(self, lease: Lease) -> AbstractContextManager[Completion]: ...

    def fail(self, lease: Lease, exc: BaseException) -> None: ...

    def release(self, lease: Lease) -> None: ...


def build_task_queue(settings: QueueSettings | None = None) -> TaskQueue:
    return DbTaskQueue(settings=settings)
