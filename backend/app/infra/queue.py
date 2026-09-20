"""비동기 작업 큐.

D13 — `infra/` 는 `Protocol` 뒤에 구현을 숨긴다. 도메인은 어느 구현이 붙는지 모른다.

로컬 개발에서도 **SQS 구현을 그대로 쓴다.** ElasticMQ(SQS API 호환 서버)를 컨테이너로
띄우고 `SQS_ENDPOINT_URL` 로 가리키면 된다. 별도의 LocalQueue 를 두지 않는 이유는,
visibility timeout · 재시도 · DLQ 재배치를 흉내 낸 구현은 결국 진짜와 어긋나기 때문이다.
로컬에서 통과한 재시도 로직이 배포 후에 다르게 도는 것보다, 같은 구현을 쓰는 편이 낫다.

프로덕션에서는 `SQS_ENDPOINT_URL` 을 비운다. boto3 가 실제 AWS 로 붙고
인증은 인스턴스 역할이 한다 — 액세스 키를 두지 않는다.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import boto3
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.enums import TaskStatus
from app.models.task import Task


@dataclass(frozen=True)
class ReceivedTask:
    body: dict[str, Any]
    receipt: str
    # 이 메시지가 배달된 횟수. maxReceiveCount 를 넘기면 큐가 DLQ 로 옮긴다.
    receive_count: int


class TaskQueue(Protocol):
    """작업 큐. 도메인은 이 모양만 안다."""

    def send(self, body: dict[str, Any]) -> None: ...

    def receive(self, max_count: int = 1, wait_seconds: int = 5) -> list[ReceivedTask]: ...

    def delete(self, receipt: str) -> None: ...


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
    """이 횟수만큼 실패하면 FAILED 로 격리한다. SQS 의 maxReceiveCount 와 같은 값."""
    QUEUE_BACKOFF_BASE_SEC: int = 30
    """재시도 지연의 기준. 30s → 60s 로 두 배씩 민다."""
    QUEUE_IDLE_TX_TIMEOUT_SEC: int = 120
    """작업 트랜잭션의 idle_in_transaction 상한. AI 타임아웃(45초)보다 넉넉히 위여야
    정상 작업을 죽이지 않는다. 멈춘 워커가 행을 영원히 붙잡는 것만 막는 안전망이다."""

    QUEUE_TYPE: str = "sqs"
    SQS_QUEUE_URL: str = ""
    SQS_DLQ_URL: str = ""
    # 로컬 ElasticMQ 주소. 프로덕션에서는 비운다 (실제 AWS 엔드포인트를 쓴다).
    SQS_ENDPOINT_URL: str = ""
    AWS_DEFAULT_REGION: str = "ap-northeast-2"

    # ElasticMQ 는 값을 검사하지 않지만 boto3 가 서명을 만들려면 뭔가는 있어야 한다.
    # boto3 는 .env 를 읽지 않으므로(실제 환경변수만 본다) 여기서 받아 명시적으로 넘긴다.
    # 프로덕션에서는 비운다 — 그러면 boto3 가 인스턴스 역할로 떨어진다.
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""


class SqsQueue:
    """SQS(및 API 호환 서버) 구현."""

    def __init__(
        self,
        queue_url: str,
        *,
        endpoint_url: str | None = None,
        region_name: str = "ap-northeast-2",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        self._url = queue_url
        # 빈 문자열은 전부 None 으로 넘긴다 — 그래야 boto3 가 기본 동작으로 떨어진다.
        # 엔드포인트는 실제 AWS 로, 자격증명은 인스턴스 역할로.
        self._client = boto3.client(
            "sqs",
            endpoint_url=endpoint_url or None,
            region_name=region_name,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
        )

    def send(self, body: dict[str, Any]) -> None:
        self._client.send_message(
            QueueUrl=self._url,
            MessageBody=json.dumps(body, ensure_ascii=False),
        )

    def receive(self, max_count: int = 1, wait_seconds: int = 5) -> list[ReceivedTask]:
        response = self._client.receive_message(
            QueueUrl=self._url,
            MaxNumberOfMessages=max_count,
            WaitTimeSeconds=wait_seconds,
            MessageAttributeNames=["All"],
            AttributeNames=["ApproximateReceiveCount"],
        )
        return [
            ReceivedTask(
                body=json.loads(message["Body"]),
                receipt=message["ReceiptHandle"],
                receive_count=int(message["Attributes"]["ApproximateReceiveCount"]),
            )
            for message in response.get("Messages", [])
        ]

    def delete(self, receipt: str) -> None:
        """처리에 **성공했을 때만** 부른다.

        실패했는데 지우면 재시도도 DLQ 도 일어나지 않는다.
        지우지 않으면 visibility timeout 이 지난 뒤 다시 배달된다.
        """
        self._client.delete_message(QueueUrl=self._url, ReceiptHandle=receipt)


def build_task_queue(settings: QueueSettings | None = None, *, dlq: bool = False) -> TaskQueue:
    settings = settings or QueueSettings()

    if settings.QUEUE_TYPE != "sqs":
        raise NotImplementedError(
            f"QUEUE_TYPE={settings.QUEUE_TYPE} 은 구현돼 있지 않다. "
            "로컬에서도 sqs 를 쓴다 — infra/docker-compose.queue.yml 로 ElasticMQ 를 띄울 것."
        )

    url = settings.SQS_DLQ_URL if dlq else settings.SQS_QUEUE_URL
    if not url:
        name = "SQS_DLQ_URL" if dlq else "SQS_QUEUE_URL"
        raise ValueError(f"{name} 이 비어 있다.")

    return SqsQueue(
        url,
        endpoint_url=settings.SQS_ENDPOINT_URL,
        region_name=settings.AWS_DEFAULT_REGION,
        access_key_id=settings.AWS_ACCESS_KEY_ID,
        secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


def enqueue(db: Session, task_type: str, payload: dict[str, Any]) -> None:
    """작업을 넣는다. **커밋하지 않는다.**

    호출부(service)가 쓰던 세션을 그대로 받아 INSERT 만 한다. 그래서 도메인 변경과
    작업 등록이 한 트랜잭션이다 — `meals` INSERT 는 됐는데 작업은 안 들어가는(또는
    그 반대인) 상태가 애초에 만들어지지 않는다.

    세션을 인자로 받는 게 핵심이다. 여기서 자기 세션을 열어 커밋해 버리면 SQS 때와
    똑같이 "커밋 순서를 사람이 지켜야 하는" 문제로 돌아간다.
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
            except BaseException as exc:
                db.rollback()
                self._record_failure(claim.task, exc)
                raise

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

        # 예외 메시지에 사용자 입력이 섞여 들어올 수 있다. 타입과 앞부분만 남긴다(규칙 6).
        reason = f"{type(exc).__name__}: {exc}"[:_ERROR_MAX_CHARS]

        try:
            with self._session_factory() as db:
                db.execute(
                    update(Task)
                    .where(Task.id == task.id)
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
