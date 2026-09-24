# DB 테이블 큐 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ElasticMQ/SQS 로 돌던 비동기 작업 큐를 PostgreSQL 테이블 하나(`task_queue`)로 옮긴다.

**Architecture:** 워커는 `SELECT … FOR UPDATE SKIP LOCKED` 로 행 하나를 집고 **처리하는 동안 잠금을 유지**한다. 성공하면 같은 트랜잭션에서 `status='DONE'` + 결과를 커밋하고, 실패하면 롤백해 작업이 저절로 되돌아가되 별도 세션으로 `attempts`·`last_error`·`next_run_at`(backoff)을 남긴다. 3회 실패하면 `FAILED` 로 격리한다(= DLQ). 작업 등록은 도메인 트랜잭션에 INSERT 만 하므로 도메인 커밋과 원자적이다.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0(동기 Session) · Alembic · PostgreSQL 17 · pytest + testcontainers

**Spec:** `docs/superpowers/specs/2026-09-20-db-table-queue-design.md`

## Global Constraints

- 주석·문서·커밋 메시지는 **한국어**. 기존 파일들의 "왜 이렇게 했는지"를 설명하는 톤을 따른다.
- 커밋 메시지 접두사는 `[BE]`. 기존 이력의 `[BE-5]` 는 번호를 붙이지 않기로 해서 `[BE]` 를 쓴다.
- **규칙 6 — 민감정보를 로그·에러 컬럼에 남기지 않는다.** 음식명, 이미지 키, 사용자 원문은 `last_error` 에도 로그에도 넣지 않는다.
- **규칙 5 — `crud/` 가 유일한 DB 접근 지점**이지만 큐는 인프라라 `infra/queue.py` 가 직접 SQL 을 친다. 기존 `infra/` 가 그런 것처럼 도메인 테이블은 건드리지 않는다.
- 모델 이름은 `Task`(테이블 `task_queue`). `TaskQueue` 는 `infra/queue.py` 의 Protocol 이름으로 남긴다.
- `QUEUE_MAX_ATTEMPTS=3`, `QUEUE_BACKOFF_BASE_SEC=30`, `QUEUE_POLL_INTERVAL_SEC=1.0`, `QUEUE_IDLE_TX_TIMEOUT_SEC=120`.
- 테스트는 `cd backend` 후 실행한다: `.\.venv\Scripts\Activate.ps1; pytest ...`. Postgres testcontainer 가 뜨므로 Docker 가 켜져 있어야 한다.
- 작업 순서를 지킨다. **모든 커밋 시점에 `pytest` 가 전부 통과해야 한다.** Task 2·3 에서 SQS 코드가 잠시 공존하는 것은 의도된 것이다.

## File Structure

| 파일 | 책임 | 태스크 |
|---|---|---|
| `backend/app/models/enums.py` | `TaskStatus` 추가 | 1 |
| `backend/app/models/task.py` (신규) | `Task` 모델 = `task_queue` 행 | 1 |
| `backend/app/models/__init__.py` | `Task` import (Alembic 발견용) | 1 |
| `backend/alembic/versions/*_add_task_queue.py` (신규) | 테이블·ENUM·부분 인덱스 | 1 |
| `backend/app/infra/queue.py` | `enqueue` · `DbTaskQueue.claim` · `QueueSettings` | 2·3·4 |
| `backend/app/worker/loop.py` | 폴링 + 커밋 시점 | 4 |
| `backend/app/worker/dispatch.py` | 타입 → 핸들러 | 4 |
| `backend/app/worker/jobs/*.py` | 작업 4종 스텁의 시그니처 | 4 |
| `backend/app/worker_main.py` | 엔트리포인트 | 4 |
| `backend/app/tests/test_task_queue.py` (신규) | 큐 동작 (실제 Postgres) | 1·2·3 |
| `backend/app/tests/test_worker_loop.py` | 루프의 커밋 시점 (가짜 큐) | 4 |
| `backend/scripts/queue_status.py` | 운영 조회 | 5 |
| `backend/scripts/smoke_queue_ai.py` | 큐 ↔ ai-stub 왕복 확인 | 5 |
| 문서·compose·.env | ElasticMQ 흔적 제거 | 6 |

**삭제:** `backend/app/tests/test_queue_integration.py`, `infra/docker-compose.queue.yml`, `infra/elasticmq.conf` (태스크 4).

---

### Task 1: `Task` 모델 · `TaskStatus` ENUM · 마이그레이션

**Files:**
- Create: `backend/app/models/task.py`
- Create: `backend/alembic/versions/20260920_XXXX_<rev>_add_task_queue.py` (파일명은 alembic 이 생성)
- Create: `backend/app/tests/test_task_queue.py`
- Modify: `backend/app/models/enums.py` (파일 끝의 `pg_enum` 함수 **위**에 클래스 추가)
- Modify: `backend/app/models/__init__.py`

**Interfaces:**
- Consumes: `app.db.base.Base`, `app.models.mixins.uuid_pk/created_at/updated_at`, `app.models.enums.pg_enum`
- Produces: `app.models.task.Task` (컬럼 `id, type, payload, status, attempts, next_run_at, result, last_error, created_at, updated_at, finished_at`), `app.models.enums.TaskStatus` (`PENDING|DONE|FAILED`), 테이블 `task_queue`, 인덱스 `ix_task_queue_pending`

- [ ] **Step 1: `TaskStatus` 를 enums.py 에 추가**

`app/models/enums.py` 의 `FeedbackPeriodType` 클래스 다음, `def pg_enum(...)` 앞에 넣는다.

```python
class TaskStatus(str, enum.Enum):
    """`task_queue` 행의 상태.

    RUNNING 이 없다. 워커는 처리하는 동안 행 잠금을 쥐고 있을 뿐이고, 그 사실은
    커밋 전이라 다른 세션에 보이지 않는다 — 써 봐야 아무도 관측할 수 없는 값이 된다.
    "지금 처리 중"은 곧 "PENDING 인데 행 잠금이 걸린 상태"이고 pg_locks 로 본다.

    FAILED 는 DLQ 자리다. QUEUE_MAX_ATTEMPTS 만큼 실패하면 여기로 옮기고 더 집지 않는다.
    """

    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`app/tests/test_task_queue.py` 를 새로 만든다.

```python
"""DB 테이블 큐.

실제 PostgreSQL 을 상대로 돈다(conftest 의 testcontainer). SQLite 로 대체할 수 없다 —
이 큐의 전부가 `SELECT … FOR UPDATE SKIP LOCKED` 와 트랜잭션 경계이기 때문이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import TaskStatus
from app.models.task import Task


@pytest.fixture
def sessions(test_engine):
    """진짜로 커밋하는 세션 팩토리.

    conftest 의 `db` 픽스처는 savepoint 위에서 도는 단일 커넥션이라 여기 쓸 수 없다.
    이 테스트가 검증하는 게 바로 커밋·롤백 경계이고, 동시성 테스트에는 커넥션이 둘 필요하다.
    대신 테스트마다 테이블을 비운다.
    """
    factory = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
    yield factory
    with factory() as cleanup:
        cleanup.execute(text("DELETE FROM task_queue"))
        cleanup.commit()


def _row(db: Session, task_id) -> Task:
    return db.execute(select(Task).where(Task.id == task_id)).scalar_one()


def test_new_task_defaults_to_pending_and_runnable_now(sessions):
    """넣기만 하면 곧바로 집을 수 있는 상태여야 한다."""
    with sessions() as db:
        task = Task(type="meal.analyze", payload={"mealId": "m1"})
        db.add(task)
        db.commit()
        db.refresh(task)

        assert task.status is TaskStatus.PENDING
        assert task.attempts == 0
        assert task.result is None
        assert task.last_error is None
        assert task.finished_at is None
        assert task.next_run_at <= datetime.now(timezone.utc) + timedelta(seconds=1)
```

- [ ] **Step 3: 테스트가 실패하는 것을 확인한다**

Run: `pytest app/tests/test_task_queue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.task'`

- [ ] **Step 4: 모델을 만든다**

`app/models/task.py`:

```python
"""작업 큐 행.

큐를 별도 미들웨어로 두지 않고 테이블 하나로 처리한다. 워커는 SELECT … FOR UPDATE
SKIP LOCKED 로 한 행을 집고, **처리하는 동안 잠금을 유지한다.** 그래서 실패하면
롤백만으로 작업이 되돌아간다 — 재배달 타이머가 따로 없다.

설계 배경은 docs/superpowers/specs/2026-09-20-db-table-queue-design.md 에 있다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import TaskStatus, pg_enum
from app.models.mixins import created_at, updated_at, uuid_pk


class Task(Base):
    __tablename__ = "task_queue"

    id: Mapped[uuid.UUID] = uuid_pk()

    type: Mapped[str] = mapped_column(Text, nullable=False)
    """`meal.analyze` 등. 워커의 dispatch 키다."""
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    """작업 본문. `type` 은 여기 넣지 않는다 — 컬럼이 이미 들고 있다."""

    status: Mapped[TaskStatus] = mapped_column(
        pg_enum(TaskStatus, "task_status"),
        nullable=False,
        server_default=TaskStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    """지금까지 **실패한** 횟수. 첫 시도 때는 0 이다."""
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    """이 시각 전에는 집지 않는다. 실패할 때마다 backoff 만큼 뒤로 민다."""

    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    """성공한 작업의 핸들러 반환값."""
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    """마지막 실패 사유. **민감정보를 넣지 않는다** (규칙 6) — 예외 타입과 메시지만."""

    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # 집는 쿼리 전용 부분 인덱스. DONE 행이 쌓여도 이 인덱스는 커지지 않는다.
        Index(
            "ix_task_queue_pending",
            "next_run_at",
            "created_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )
```

- [ ] **Step 5: `models/__init__.py` 에 등록한다**

import 줄과 `__all__` 항목을 알파벳 순서를 지켜 추가한다. (`from app.models.medication import …` 다음 줄, `__all__` 에서는 `"SatietyLog"` 다음)

```python
from app.models.task import Task
```

```python
    "Task",
```

- [ ] **Step 6: 마이그레이션을 생성한다**

Run: `alembic revision --autogenerate -m "add task_queue"`

autogenerate 는 부분 인덱스의 `WHERE` 절과 ENUM 생성/삭제를 제대로 못 낸다. 생성된 파일을 아래 모양으로 **손으로 고친다.** `down_revision` 이 `'fb9324353300'` 인지 반드시 확인한다.

```python
"""add task_queue

Revision ID: <생성된 값>
Revises: fb9324353300
Create Date: <생성된 값>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '<생성된 값>'
down_revision: Union[str, Sequence[str], None] = 'fb9324353300'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ENUM 은 테이블보다 먼저 만들고 테이블을 지운 뒤에 지운다 (초기 마이그레이션과 같은 방식).
TASK_STATUS = postgresql.ENUM(
    'PENDING', 'DONE', 'FAILED', name='task_status', create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    TASK_STATUS.create(bind, checkfirst=True)

    op.create_table(
        'task_queue',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', TASK_STATUS, server_default='PENDING', nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'next_run_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_task_queue')),
    )

    # 집는 쿼리 전용 부분 인덱스. autogenerate 는 WHERE 절을 만들지 못한다.
    op.create_index(
        'ix_task_queue_pending',
        'task_queue',
        ['next_run_at', 'created_at'],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_task_queue_pending',
        table_name='task_queue',
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.drop_table('task_queue')
    TASK_STATUS.drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 7: 테스트가 통과하는지 확인한다**

Run: `pytest app/tests/test_task_queue.py -v`
Expected: PASS (1 passed). testcontainer 기동에 수 초 걸린다.

- [ ] **Step 8: 마이그레이션이 되돌려지는지 확인한다**

로컬 DB(`infra/docker-compose.yml` 의 `glp1-db`)를 상대로:

Run: `alembic upgrade head; alembic downgrade -1; alembic upgrade head`
Expected: 세 번 다 에러 없이 끝난다. downgrade 가 `task_status` 타입까지 지우지 않으면 두 번째 upgrade 가 `type "task_status" already exists` 로 깨진다.

- [ ] **Step 9: 커밋**

```bash
git add backend/app/models/task.py backend/app/models/enums.py backend/app/models/__init__.py backend/alembic/versions backend/app/tests/test_task_queue.py
git commit -m "[BE] feat: task_queue 테이블과 Task 모델 추가"
```

---

### Task 2: `enqueue()` — 도메인 트랜잭션에 INSERT 만 한다

**Files:**
- Modify: `backend/app/infra/queue.py` (파일 끝에 추가. 기존 SQS 코드는 **건드리지 않는다**)
- Modify: `backend/app/tests/test_task_queue.py`

**Interfaces:**
- Consumes: `app.models.task.Task` (Task 1)
- Produces: `app.infra.queue.enqueue(db: Session, task_type: str, payload: dict[str, Any]) -> None`

> 이 태스크에서는 SQS 코드(`SqsQueue`, `ReceivedTask`, 기존 `TaskQueue` Protocol)를 그대로 둔다. 워커가 아직 그걸 쓰고 있어서, 지우면 테스트가 깨진다. 제거는 Task 4 다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`app/tests/test_task_queue.py` 끝에 추가한다.

```python
# ─────────────────────────── 넣기 ───────────────────────────


def test_enqueue_is_atomic_with_the_domain_transaction(sessions):
    """도메인 커밋이 롤백되면 작업도 같이 사라진다.

    SQS 를 쓸 때는 이게 불가능해서 "커밋이 먼저다" 라는 규칙을 사람이 지켜야 했다.
    """
    from app.infra.queue import enqueue

    with sessions() as db:
        enqueue(db, "meal.analyze", {"mealId": "m1"})
        db.rollback()

    with sessions() as db:
        assert db.execute(select(Task)).scalars().all() == []


def test_enqueue_writes_the_row_on_commit(sessions):
    from app.infra.queue import enqueue

    with sessions() as db:
        enqueue(db, "meal.analyze", {"mealId": "m1"})
        db.commit()

    with sessions() as db:
        task = db.execute(select(Task)).scalar_one()
        assert task.type == "meal.analyze"
        assert task.payload == {"mealId": "m1"}
        assert task.status is TaskStatus.PENDING
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `pytest app/tests/test_task_queue.py -v -k enqueue`
Expected: FAIL — `ImportError: cannot import name 'enqueue' from 'app.infra.queue'`

- [ ] **Step 3: `enqueue` 를 구현한다**

`app/infra/queue.py` 끝에 추가한다. 파일 위쪽 import 에 두 줄을 더한다.

```python
from sqlalchemy.orm import Session

from app.models.task import Task
```

```python
def enqueue(db: Session, task_type: str, payload: dict[str, Any]) -> None:
    """작업을 넣는다. **커밋하지 않는다.**

    호출부(service)가 쓰던 세션을 그대로 받아 INSERT 만 한다. 그래서 도메인 변경과
    작업 등록이 한 트랜잭션이다 — `meals` INSERT 는 됐는데 작업은 안 들어가는(또는
    그 반대인) 상태가 애초에 만들어지지 않는다.

    세션을 인자로 받는 게 핵심이다. 여기서 자기 세션을 열어 커밋해 버리면 SQS 때와
    똑같이 "커밋 순서를 사람이 지켜야 하는" 문제로 돌아간다.
    """
    db.add(Task(type=task_type, payload=payload))
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `pytest app/tests/test_task_queue.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: 전체 테스트가 여전히 통과하는지 확인한다**

Run: `pytest`
Expected: 기존과 동일. `test_queue_integration.py` 는 ElasticMQ 가 안 떠 있으면 skip 된다 — 정상이다.

- [ ] **Step 6: 커밋**

```bash
git add backend/app/infra/queue.py backend/app/tests/test_task_queue.py
git commit -m "[BE] feat: 도메인 트랜잭션에 작업을 넣는 enqueue 추가"
```

---

### Task 3: `DbTaskQueue.claim()` — 집기·성공·실패·격리

**Files:**
- Modify: `backend/app/infra/queue.py`
- Modify: `backend/app/tests/test_task_queue.py`

**Interfaces:**
- Consumes: `app.models.task.Task`, `app.models.enums.TaskStatus`, `app.db.session.SessionLocal`
- Produces:
  - `ClaimedTask(id: UUID, type: str, payload: dict, attempts: int)` — frozen dataclass
  - `Claim(db: Session, task: ClaimedTask, result: dict | None = None)` — mutable dataclass
  - `DbTaskQueue(session_factory=SessionLocal, settings: QueueSettings | None = None)` with `claim() -> ContextManager[Claim | None]`
  - `QueueSettings` 에 `QUEUE_POLL_INTERVAL_SEC: float = 1.0`, `QUEUE_MAX_ATTEMPTS: int = 3`, `QUEUE_BACKOFF_BASE_SEC: int = 30`, `QUEUE_IDLE_TX_TIMEOUT_SEC: int = 120` 추가

> Protocol 은 여기서 정의하지 않는다. 기존 `TaskQueue` Protocol(send/receive/delete)이 아직 살아 있어 이름이 부딪히기 때문이다. 교체는 Task 4 다.

- [ ] **Step 1: 실패하는 테스트를 쓴다 — 집기와 성공**

`app/tests/test_task_queue.py` 끝에 추가한다.

```python
# ─────────────────────────── 집기 ───────────────────────────


@pytest.fixture
def queue(sessions):
    from app.infra.queue import DbTaskQueue, QueueSettings

    return DbTaskQueue(
        sessions,
        settings=QueueSettings(QUEUE_MAX_ATTEMPTS=3, QUEUE_BACKOFF_BASE_SEC=30),
    )


def _put(sessions, **kwargs) -> Task:
    task = Task(type=kwargs.pop("type", "meal.analyze"), payload=kwargs.pop("payload", {"mealId": "m1"}), **kwargs)
    with sessions() as db:
        db.add(task)
        db.commit()
        db.refresh(task)
    return task


def test_claim_returns_none_on_empty_queue(queue):
    with queue.claim() as claim:
        assert claim is None


def test_success_commits_done_with_the_handler_result(sessions, queue):
    put = _put(sessions)

    with queue.claim() as claim:
        assert claim is not None
        assert claim.task.id == put.id
        assert claim.task.type == "meal.analyze"
        assert claim.task.payload == {"mealId": "m1"}
        assert claim.task.attempts == 0
        claim.result = {"items": 3}

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.DONE
        assert row.result == {"items": 3}
        assert row.finished_at is not None
        assert row.attempts == 0


def test_claimed_task_is_not_handed_out_twice(sessions, queue):
    """DONE 이 된 작업은 다시 집히지 않는다."""
    _put(sessions)

    with queue.claim() as claim:
        assert claim is not None

    with queue.claim() as claim:
        assert claim is None
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `pytest app/tests/test_task_queue.py -v -k "claim or success"`
Expected: FAIL — `ImportError: cannot import name 'DbTaskQueue' from 'app.infra.queue'`

- [ ] **Step 3: `QueueSettings` 에 큐 설정을 더한다**

`app/infra/queue.py` 의 기존 `QueueSettings` 클래스 안, `QUEUE_TYPE` 줄 **위**에 추가한다. SQS 필드는 Task 4 에서 지운다.

```python
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
```

- [ ] **Step 4: `ClaimedTask` · `Claim` · `DbTaskQueue` 를 구현한다**

`app/infra/queue.py` 의 `enqueue` 아래에 추가한다. 파일 위쪽 import 에 더한다.

```python
import logging
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import timedelta

from sqlalchemy import func, select, update

from app.db.session import SessionLocal
from app.models.enums import TaskStatus
```

```python
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
```

`Callable` 과 `text` 는 import 에 있어야 한다. 파일 위쪽에서 `from collections.abc import Callable, Iterator`, `from sqlalchemy import func, select, text, update` 를 확인한다.

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `pytest app/tests/test_task_queue.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: 실패·격리·backoff 테스트를 쓴다**

`app/tests/test_task_queue.py` 끝에 추가한다.

```python
# ─────────────────────────── 실패 ───────────────────────────


def test_failure_leaves_the_task_pending_and_records_the_attempt(sessions, queue):
    """실패하면 지우지도 완료하지도 않는다. 이력만 남기고 되돌린다."""
    put = _put(sessions)
    before = datetime.now(timezone.utc)

    with pytest.raises(RuntimeError):
        with queue.claim() as claim:
            assert claim is not None
            raise RuntimeError("AI 가 500 을 냈다")

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert row.attempts == 1
        assert "RuntimeError" in row.last_error
        assert row.result is None
        # 첫 실패 → 30초 뒤. 곧바로 다시 집으면 재시도가 순식간에 소진된다.
        assert row.next_run_at > before + timedelta(seconds=20)


def test_domain_writes_are_rolled_back_with_the_task(sessions, queue):
    """핸들러가 claim 의 세션에 쓴 것도 함께 되돌아간다.

    이게 SQS 대비 가장 큰 차이다 — 재시도할 때 이전 시도의 흔적이 남지 않는다.
    """
    put = _put(sessions)

    with pytest.raises(RuntimeError):
        with queue.claim() as claim:
            claim.db.add(Task(type="side.effect", payload={}))
            claim.db.flush()
            raise RuntimeError("처리 도중 실패")

    with sessions() as db:
        types = db.execute(select(Task.type)).scalars().all()
        assert types == ["meal.analyze"]
        assert _row(db, put.id).attempts == 1


def test_task_is_quarantined_after_max_attempts(sessions, queue):
    """3회째 실패하면 FAILED 로 옮기고 더 집지 않는다 — DLQ 자리다."""
    put = _put(sessions, attempts=2)

    with pytest.raises(RuntimeError):
        with queue.claim() as claim:
            raise RuntimeError("세 번째 실패")

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.FAILED
        assert row.attempts == 3

    with queue.claim() as claim:
        assert claim is None


def test_task_scheduled_in_the_future_is_not_claimed(sessions, queue):
    _put(sessions, next_run_at=datetime.now(timezone.utc) + timedelta(hours=1))

    with queue.claim() as claim:
        assert claim is None
```

- [ ] **Step 7: 테스트가 통과하는지 확인한다**

Run: `pytest app/tests/test_task_queue.py -v`
Expected: PASS (10 passed). 실패하면 구현을 고친다 — 테스트가 맞다.

- [ ] **Step 8: SKIP LOCKED 동시성 테스트를 쓴다**

이 큐의 존재 이유다. 반드시 커넥션 둘로 확인한다.

```python
# ─────────────────────────── 동시성 ───────────────────────────


def test_two_workers_never_claim_the_same_row(sessions, queue):
    """SKIP LOCKED — 워커 둘이 동시에 집으면 서로 다른 행을 가져간다.

    잠긴 행을 기다리지 않고 건너뛴다는 것이 핵심이다. 기다리면 워커를 늘려도
    한 줄로 서게 된다.
    """
    from app.infra.queue import DbTaskQueue

    first = _put(sessions, payload={"n": 1})
    second = _put(sessions, payload={"n": 2})
    other = DbTaskQueue(sessions)

    with queue.claim() as a, other.claim() as b:
        assert a is not None and b is not None
        assert {a.task.id, b.task.id} == {first.id, second.id}


def test_second_worker_gets_nothing_when_the_only_row_is_locked(sessions, queue):
    from app.infra.queue import DbTaskQueue

    _put(sessions)
    other = DbTaskQueue(sessions)

    with queue.claim() as a:
        assert a is not None
        with other.claim() as b:
            assert b is None
```

- [ ] **Step 9: 테스트가 통과하는지 확인한다**

Run: `pytest app/tests/test_task_queue.py -v`
Expected: PASS (12 passed)

- [ ] **Step 10: 전체 테스트를 돌린다**

Run: `pytest`
Expected: 기존 테스트 전부 통과 (ElasticMQ 통합 테스트는 skip)

- [ ] **Step 11: 커밋**

```bash
git add backend/app/infra/queue.py backend/app/tests/test_task_queue.py
git commit -m "[BE] feat: SELECT FOR UPDATE SKIP LOCKED 기반 claim 구현"
```

---

### Task 4: 워커를 새 인터페이스로 옮기고 ElasticMQ 를 걷어낸다

**Files:**
- Modify: `backend/app/infra/queue.py` (SQS 코드 삭제 + Protocol 교체 + 모듈 docstring)
- Modify: `backend/app/worker/loop.py`, `backend/app/worker/dispatch.py`, `backend/app/worker_main.py`
- Modify: `backend/app/worker/jobs/analyze_meal.py`, `feedback_meal.py`, `feedback_daily.py`, `feedback_long.py`
- Modify: `backend/app/tests/test_worker_loop.py`
- Modify: `backend/requirements.txt`
- Delete: `backend/app/tests/test_queue_integration.py`, `backend/scripts/queue_status.py`, `backend/scripts/smoke_queue_ai.py`, `infra/docker-compose.queue.yml`, `infra/elasticmq.conf`

**Interfaces:**
- Consumes: `DbTaskQueue`, `Claim`, `ClaimedTask`, `QueueSettings` (Task 3)
- Produces:
  - `app.infra.queue.TaskQueue` Protocol — `claim() -> AbstractContextManager[Claim | None]`
  - `app.infra.queue.build_task_queue() -> TaskQueue`
  - `app.worker.dispatch.handle(db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None`
  - `app.worker.jobs.<name>.run(db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None`

> 스크립트 둘은 여기서 지우고 Task 5 에서 새로 쓴다. 지금 지우지 않으면 `build_task_queue(dlq=True)` 를 import 해서 깨진다.

- [ ] **Step 1: 워커 루프 테스트를 새 인터페이스로 다시 쓴다**

`app/tests/test_worker_loop.py` 를 통째로 아래로 교체한다.

```python
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
```

- [ ] **Step 2: 테스트가 실패하는 것을 확인한다**

Run: `pytest app/tests/test_worker_loop.py -v`
Expected: FAIL — `Claim` 은 Task 3 에서 이미 만들었으니 import 는 통과하고, 인자 개수가 맞지 않아 깨진다 (`TypeError: handle() takes 2 positional arguments but 3 were given`)

- [ ] **Step 3: `dispatch.py` 를 고친다**

시그니처와 분기 기준만 바뀐다. 파일 앞부분의 긴 설명 주석은 그대로 두되, `_HANDLERS` 위의 타입과 `handle` 을 아래로 바꾼다. 문서 절 "Q/Q/S 채점은 작업이 아니다" 아래 문장 중 `queue.send` 언급이 있으면 `enqueue` 로 고친다.

```python
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

    반환값은 `task_queue.result` 에 남는다. 남길 게 없으면 None.
    """
    handler = _HANDLERS.get(task.type)
    if handler is not None:
        return handler(db, task, ai)

    if task.type in _NOT_IMPLEMENTED:
        raise NotImplementedError(f"{_NOT_IMPLEMENTED[task.type]} 미구현")

    raise ValueError(f"알 수 없는 작업 타입: {task.type!r}")
```

- [ ] **Step 4: `loop.py` 를 고친다**

파일 전체를 아래로 교체한다.

```python
"""큐 폴링 루프.

**커밋 시점이 이 파일의 전부다.** 처리에 성공했을 때만 `claim` 블록을 정상으로
빠져나가 DONE 이 커밋된다. 예외가 나면 롤백돼 작업이 PENDING 으로 되돌아가고,
QUEUE_MAX_ATTEMPTS 를 넘기면 큐가 FAILED 로 격리한다.

작업 핸들러와 파일을 나눈 건 이 때문이다. 여기가 틀리면 작업이 조용히 사라지는데,
핸들러를 붙이다가 실수로 건드리기 쉬운 자리에 두고 싶지 않다.
"""

from __future__ import annotations

import logging
import time

from app.infra.ai import AiClient
from app.infra.queue import QueueSettings, TaskQueue
from app.worker.dispatch import handle

logger = logging.getLogger("worker")

_running = True


def request_stop() -> None:
    """루프를 멈춘다. 처리 중인 작업은 끝까지 간다."""
    global _running
    _running = False


def run(queue: TaskQueue, ai: AiClient, settings: QueueSettings | None = None) -> None:
    settings = settings or QueueSettings()
    logger.info("워커 시작. 큐를 폴링한다.")

    while _running:
        try:
            with queue.claim() as claim:
                if claim is None:
                    # 롱 폴링이 없으니 직접 쉰다. 안 쉬면 빈 테이블을 쉬지 않고 때린다.
                    time.sleep(settings.QUEUE_POLL_INTERVAL_SEC)
                    continue

                claim.result = handle(claim.db, claim.task, ai)
                logger.info("작업 완료 type=%s", claim.task.type)
        except Exception:
            # 큐가 롤백하고 실패를 기록했다. 여기서는 로그만 남긴다.
            # 포즈 정보가 로그에 남지 않도록 본문은 찍지 않는다(규칙 6).
            logger.exception("작업 처리 실패. 재시도에 맡긴다.")

    logger.info("워커 종료.")
```

- [ ] **Step 5: `infra/queue.py` 에서 SQS 를 걷어내고 Protocol 을 교체한다**

지울 것: `ReceivedTask`, 기존 `TaskQueue` Protocol(send/receive/delete), `SqsQueue`, `import boto3`, `import json`, `QueueSettings` 의 `QUEUE_TYPE`·`SQS_*`·`AWS_*` 필드.

모듈 docstring 을 바꾸고, `build_task_queue` 를 아래로 교체한다.

```python
"""비동기 작업 큐 — PostgreSQL 테이블.

D13 — `infra/` 는 `Protocol` 뒤에 구현을 숨긴다. 도메인은 어느 구현이 붙는지 모른다.

큐 미들웨어를 따로 두지 않는다. 작업량이 사용자 행동 하나당 하나 규모라 테이블
하나로 충분하고, 그 대신 **작업 등록이 도메인 커밋과 같은 트랜잭션**이 된다 —
"커밋이 먼저다" 라는, 사람이 지켜야 했던 순서 규칙이 사라진다.

워커는 `SELECT … FOR UPDATE SKIP LOCKED` 로 행 하나를 집고 처리하는 동안 잠금을
유지한다. 실패하면 롤백만으로 작업이 되돌아간다.

설계 배경은 docs/superpowers/specs/2026-09-20-db-table-queue-design.md 에 있다.
"""
```

```python
class TaskQueue(Protocol):
    """작업 큐. 워커는 이 모양만 안다."""

    def claim(self) -> AbstractContextManager[Claim | None]: ...


def build_task_queue() -> TaskQueue:
    return DbTaskQueue()
```

`from contextlib import AbstractContextManager, contextmanager` 로 import 를 맞춘다.

- [ ] **Step 6: `worker_main.py` 를 고친다**

`loop.run(build_task_queue(), build_ai_client())` 는 그대로 둔다 — `build_task_queue()` 의 반환이 바뀔 뿐이다. 모듈 docstring 의 "API 는 큐에 넣고 202 를 돌려줄 뿐" 문단 뒤에 한 줄 더한다.

```
큐는 PostgreSQL 테이블(`task_queue`)이다. 컨테이너를 따로 띄우지 않는다.
```

- [ ] **Step 7: jobs 4개의 시그니처를 바꾼다**

넷 다 같은 모양으로 고친다. `analyze_meal.py` 를 예로 들면:

import 에서 `from app.db.session import SessionLocal` 을 지우고 아래를 더한다.

```python
from typing import Any

from sqlalchemy.orm import Session

from app.infra.queue import ClaimedTask
```

시그니처와 본문 틀:

```python
def run(db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None:
```

본문에서 `body = task.body` → `body = task.payload`, `with SessionLocal() as db:` 블록을 없애고 한 단 들여쓰기를 푼다. 블록 끝의 `db.commit()` 을 지우고 그 자리에 주석을 남긴다.

```python
    # 커밋하지 않는다. 이 세션은 큐가 작업을 잠근 트랜잭션이고, 큐가 DONE 과 함께
    # 한 번에 커밋한다. 여기서 터지면 도메인 변경까지 통째로 롤백된다.
```

각 파일의 docstring 에서 재배달 전제를 고친다.

- `analyze_meal.py` — "SQS 는 at-least-once 라 같은 메시지가 두 번 올 수 있다" 문단을 아래로 바꾼다. **status 검사는 남긴다.**

```
    실패한 시도의 도메인 변경은 큐 트랜잭션과 함께 롤백되므로, 재시도할 때 이전
    시도의 흔적은 없다. 그래도 `meals.status` 가 `ANALYZING` 이 아니면 넘어간다 —
    같은 작업이 두 번 등록되는 경우(사용자 더블 탭 등)는 큐 구현과 무관하게 남는다.
```

  구현 순서 6번 "이 식사의 기존 모델 생성 항목을 지우고 새로 넣는다(재배달 대비)" 는
  "이 식사의 기존 모델 생성 항목을 지우고 새로 넣는다(두 번 등록된 경우 대비)" 로 고친다.

- `feedback_daily.py` · `feedback_meal.py` · `feedback_long.py` — "안 지우면 재배달마다 같은 근거가 쌓인다" 류의 문장을 "안 지우면 같은 작업이 두 번 등록됐을 때 같은 근거가 쌓인다" 로 고친다. upsert 는 그대로 둔다.

- 넷 다 마지막 줄의 안내를 고친다.

```python
    # 구현이 끝나면 이 줄을 지우고 dispatch._HANDLERS 에 등록한다.
    # 먼저 지우면 loop.py 가 성공으로 보고 DONE 을 커밋한다 — 작업이 조용히 사라진다.
    raise NotImplementedError("meal.analyze 미구현")
```

- [ ] **Step 8: 테스트가 통과하는지 확인한다**

Run: `pytest app/tests/test_worker_loop.py -v`
Expected: PASS (9 passed)

- [ ] **Step 9: ElasticMQ 파일과 의존을 지운다**

```bash
git rm backend/app/tests/test_queue_integration.py
git rm backend/scripts/queue_status.py backend/scripts/smoke_queue_ai.py
git rm infra/docker-compose.queue.yml infra/elasticmq.conf
```

`backend/requirements.txt` 에서 두 줄을 지운다.

```
# 큐 — SQS. 로컬은 ElasticMQ(SQS API 호환)를 같은 코드로 쓴다
boto3==1.43.93
```

`__pycache__` 에 남은 `.pyc` 는 무시한다(git 에 없다).

- [ ] **Step 10: boto3 없이도 도는지 확인한다**

Run: `pip uninstall -y boto3 botocore; pytest`
Expected: 전체 통과. `boto3` 를 import 하는 코드가 하나도 남지 않았다는 뜻이다.

Run: `grep -ri "boto3\|elasticmq\|sqs" backend/app backend/scripts backend/requirements.txt`
Expected: 결과 없음

- [ ] **Step 11: 커밋**

```bash
git add -A backend/app backend/requirements.txt infra
git commit -m "[BE] refactor: 워커를 DB 테이블 큐로 옮기고 ElasticMQ 제거"
```

---

### Task 5: 운영 스크립트 둘을 다시 쓴다

**Files:**
- Create: `backend/scripts/queue_status.py`
- Create: `backend/scripts/smoke_queue_ai.py`

**Interfaces:**
- Consumes: `app.db.session.SessionLocal`, `app.infra.queue.enqueue/DbTaskQueue/QueueSettings`, `app.infra.ai.AiSettings/build_ai_client`

> 스크립트에는 테스트를 붙이지 않는다(기존에도 없었다). 대신 실제로 실행해서 결과를 눈으로 확인하는 것이 검증이다.

- [ ] **Step 1: `queue_status.py` 를 쓴다**

```python
r"""큐 상태 보기.

    cd backend
    .\.venv\Scripts\Activate.ps1
    python -m scripts.queue_status

"처리 중" 이라는 상태 컬럼은 없다. 워커는 행 잠금을 쥐고 있을 뿐이고 그건 커밋 전이라
다른 세션에 안 보인다. 그래서 대신 **작업 트랜잭션을 열어 둔 커넥션**을 보여 준다.
"""

from __future__ import annotations

from sqlalchemy import text

from app.db.session import SessionLocal

_SUMMARY = text(
    """
    SELECT status, count(*) AS n, min(created_at) AS oldest
      FROM task_queue
     GROUP BY status
     ORDER BY status
    """
)

_IN_FLIGHT = text(
    """
    SELECT pid, now() - xact_start AS elapsed
      FROM pg_stat_activity
     WHERE xact_start IS NOT NULL
       AND query ILIKE '%task_queue%'
       AND pid <> pg_backend_pid()
     ORDER BY xact_start
    """
)

_FAILED = text(
    """
    SELECT id, type, attempts, updated_at, left(last_error, 80) AS last_error
      FROM task_queue
     WHERE status = 'FAILED'
     ORDER BY updated_at DESC
     LIMIT 10
    """
)


def main() -> None:
    with SessionLocal() as db:
        print(f"{'상태':<10} {'건수':>6}  가장 오래된 것")
        print("-" * 48)
        for status, count, oldest in db.execute(_SUMMARY):
            print(f"{status:<10} {count:>6}  {oldest}")

        in_flight = db.execute(_IN_FLIGHT).all()
        print(f"\n처리 중(작업 트랜잭션을 연 커넥션): {len(in_flight)}")
        for pid, elapsed in in_flight:
            print(f"  pid={pid} 경과={elapsed}")

        failed = db.execute(_FAILED).all()
        if failed:
            print(f"\nFAILED {len(failed)}건 — 재시도를 다 쓰고 격리된 작업이다")
            for row in failed:
                print(f"  {row.type:<16} attempts={row.attempts} {row.updated_at} {row.last_error}")
            print("\n다시 넣으려면:")
            print("  UPDATE task_queue SET status='PENDING', attempts=0, next_run_at=now()")
            print("   WHERE id = '<id>';")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 실행해서 확인한다**

로컬 DB(`glp1-db`)가 떠 있어야 한다.

Run: `python -m scripts.queue_status`
Expected: 상태 표가 나온다. 큐가 비어 있으면 행이 없는 표 + "처리 중: 0" 이 정상이다.

- [ ] **Step 3: `smoke_queue_ai.py` 를 쓴다**

```python
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


def _run_once(queue: DbTaskQueue, ai) -> None:
    """작업 하나를 집어 AI 를 부른다. 스텁이 500 을 내면 예외가 그대로 올라온다."""
    try:
        with queue.claim() as claim:
            if claim is None:
                print("    집을 작업이 없다")
                return
            claim.result = ai.analyze_meal(claim.task.payload)
            print(f"    시도 {claim.task.attempts + 1}회째 — 성공")
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
```

- [ ] **Step 4: 실행해서 확인한다**

ai-stub 을 띄운 뒤:

Run: `python -m scripts.smoke_queue_ai`
Expected: `[1]` 이 DONE 으로 끝나고, `[2]` 의 attempts 가 1 → 2 → 3 으로 오르며 마지막에 `FAILED 로 격리 ✓`

- [ ] **Step 5: 커밋**

```bash
git add backend/scripts/queue_status.py backend/scripts/smoke_queue_ai.py
git commit -m "[BE] feat: DB 큐용 상태 조회·왕복 확인 스크립트"
```

---

### Task 6: 문서와 컨테이너 설정에서 ElasticMQ 를 지운다

**Files:**
- Modify: `infra/docker-compose.be.yml`
- Modify: `infra/README.md`
- Modify: `backend/README.md`
- Modify: `backend/.env.example`
- Modify: `backend/.env` (로컬 파일. 커밋하지 않는다)
- Modify: `backend/app/worker/__init__.py`

- [ ] **Step 1: `docker-compose.be.yml` 의 환경변수를 교체한다**

`x-be-env` 에서 `QUEUE_TYPE`·`SQS_*`·`AWS_*` 다섯 줄을 지우고 큐 설정을 넣는다. 파일 머리의 "먼저 띄워 둘 것" 목록에서 `docker-compose.queue.yml` 줄을 지운다.

```yaml
x-be-env: &be-env
  # 컨테이너 안에서는 서비스 이름으로 붙는다. 호스트에 매핑된 5433 이 아니라 내부 5432 다.
  DB_URL: db:5432/glp1_dev
  DB_USER: glp1
  DB_PASSWORD: glp1_local_dev
  # 큐는 위 DB 의 task_queue 테이블이다. 별도 컨테이너가 없다.
  QUEUE_POLL_INTERVAL_SEC: "1.0"
  QUEUE_MAX_ATTEMPTS: "3"
  QUEUE_BACKOFF_BASE_SEC: "30"
  AI_SERVICE_BASE_URL: http://ai-stub:8000
  # 실패 경로를 보고 싶을 때만 채운다. 예: ERROR_500
  AI_STUB_SCENARIO: ""
```

- [ ] **Step 2: `.env.example` 의 큐 절을 교체한다**

`# ── 큐 ──` 아래 `AWS_SECRET_ACCESS_KEY=test` 까지를 아래로 바꾼다. `backend/.env` 도 같은 내용으로 고친다(커밋 대상 아님).

```
# ── 큐 ────────────────────────────────────────────────────────
# 큐는 위 PostgreSQL 의 task_queue 테이블이다. 띄울 컨테이너가 없다.
# 상태는 python -m scripts.queue_status 로 본다.
QUEUE_POLL_INTERVAL_SEC=1.0
QUEUE_MAX_ATTEMPTS=3
QUEUE_BACKOFF_BASE_SEC=30
```

- [ ] **Step 3: `infra/README.md` 를 고친다**

- 컨테이너 표에서 `docker-compose.queue.yml` 행을 지운다
- "전부 띄우기" 의 두 compose 명령에서 `-f docker-compose.queue.yml` 을 지운다
- "주소가 두 벌인 이유" 표에서 `sqs` 행을 지운다
- `### node-address.host 는 건드리지 않아도 된다` 절을 통째로 지운다
- "왕복 확인" 절의 전제에서 ElasticMQ 를 빼고 ai-stub 만 남긴다

- [ ] **Step 4: `backend/README.md` 의 큐 절을 다시 쓴다**

`## 큐 사용법` 부터 `### 작업을 하나 붙이려면` 앞까지를 아래로 교체한다.

````markdown
## 큐 사용법

비동기 작업은 PostgreSQL 의 `task_queue` 테이블을 거친다. 큐 컨테이너는 없다.

### 넣기 — 도메인과 같은 트랜잭션

```python
from app.infra.queue import enqueue

def create_meal(db: Session, ...) -> MealResponse:
    meal = crud.meal.create(db, ...)
    enqueue(db, "meal.analyze", {
        "mealId": str(meal.id),
        "mealType": "LUNCH",
        "eatenAt": "2026-08-21T12:40:00+09:00",
        "stage": "MAINTENANCE",
        "rawText": "김밥 한 줄",
    })
    db.commit()     # 식사와 작업이 한 번에 들어간다
    return ...
```

`enqueue` 는 **커밋하지 않는다.** 쓰던 세션에 INSERT 만 하므로 도메인 변경과 작업
등록이 원자적이다 — 커밋이 실패하면 둘 다 없고, 성공하면 둘 다 있다. SQS 를 쓸 때
지켜야 했던 "커밋이 먼저다" 규칙은 이제 없다.

### 꺼내기 — 잠금을 쥔 채로 처리한다

```python
with queue.claim() as claim:
    if claim is None:
        ...          # 빈 큐
    claim.db         # 이 작업을 잠근 세션. 핸들러가 도메인 쓰기에 그대로 쓴다
    claim.task.type  # 'meal.analyze'
    claim.task.payload
    claim.task.attempts   # 지금까지 실패한 횟수. 첫 시도는 0
    claim.result = {...}  # 담아 두면 DONE 과 함께 result 컬럼에 들어간다
```

`SELECT … FOR UPDATE SKIP LOCKED` 로 한 행을 집고 **블록이 끝날 때까지 잠금을
유지한다.** 잠긴 행은 다른 워커가 건너뛴다. 워커를 늘리면 그대로 분산된다.

### 커밋 시점 — 여기가 전부다

| 블록이 | 큐가 하는 일 |
|---|---|
| 정상 종료 | `status='DONE'`, `result`, `finished_at` 커밋 |
| 예외 | 롤백 → 별도 트랜잭션에 `attempts+1`·`last_error`·`next_run_at`(30s→60s) → 3회째면 `FAILED` |

**롤백되면 핸들러가 쓴 도메인 변경까지 통째로 되돌아간다.** 그래서 재시도할 때
이전 시도의 흔적이 없다. 반대로 실패했는데 예외를 삼키면 큐는 성공으로 보고 DONE 을
커밋한다 — 작업이 조용히 사라진다. `app/worker/loop.py` 의 `run()` 이 이 구조이고,
`app/tests/test_worker_loop.py` 가 이것만 검증한다.

`FAILED` 는 DLQ 자리다. 자동으로 되살리지 않는다 — 3번 실패한 작업은 대개 코드나
데이터가 잘못된 것이라 사람이 원인을 보고 다시 넣는다.

```sql
UPDATE task_queue SET status='PENDING', attempts=0, next_run_at=now() WHERE id='…';
```

상태는 `python -m scripts.queue_status` 로 본다. "처리 중" 이라는 상태 컬럼은 없다 —
워커는 행 잠금을 쥐고 있을 뿐이고 그건 커밋 전이라 다른 세션에 보이지 않는다.
````

`### 작업을 하나 붙이려면` 절에서 두 곳을 고친다.

```
1. `app/worker/jobs/` 에 파일을 만들고 `run(db, task, ai)` 를 둔다
```

```
스켈레톤 마지막 줄의 `raise NotImplementedError` 를 **가장 마지막에** 지운다.
먼저 지우면 `loop.py` 가 성공으로 보고 DONE 을 커밋한다.
```

- [ ] **Step 5: `backend/README.md` 의 나머지 SQS 흔적을 고친다**

- 40행 부근 구조 설명: `├── infra/             ★  S3 · SQS · FCM · AI HTTP` → `├── infra/             ★  S3 · 큐 · FCM · AI HTTP`
- 61행 부근: `- \`TaskQueue\` → \`SqsQueue\`(prod) / \`LocalQueue\`(로컬 개발)` → `- \`TaskQueue\` → \`DbTaskQueue\` (PostgreSQL \`task_queue\` 테이블. 로컬·프로덕션 같은 구현)`
- 환경변수 표: `QUEUE_TYPE`·`SQS_*`·`AWS_DEFAULT_REGION`·`AWS_ACCESS_KEY_ID`… 행을 지우고 세 줄을 넣는다

| 키 | 설명 |
| --- | --- |
| `QUEUE_POLL_INTERVAL_SEC` | 빈 큐일 때 쉬는 시간. 기본 1.0 |
| `QUEUE_MAX_ATTEMPTS` | 이 횟수만큼 실패하면 FAILED 로 격리. 기본 3 |
| `QUEUE_BACKOFF_BASE_SEC` | 재시도 지연 기준. 기본 30 (30s → 60s) |

- 표 아래의 `> 🚨 AWS 액세스 키는 두지 않는다 …` 인용 블록에서 SQS·boto3 문단을 지운다. S3 에 대한 인스턴스 역할 문장은 남긴다.
- 테스트 전략 표의 `| Worker 루프 | 가짜 큐·가짜 AI 로 **삭제 시점**만 검증 …` 행을 아래로 바꾼다

```
| Worker 루프  | 가짜 큐·가짜 AI 로 **커밋 시점**만 검증 — 실패한 작업을 DONE 으로 커밋하지 않는지(`test_worker_loop.py`). 외부 의존 0 |
| 작업 큐      | 실제 Postgres 로 SKIP LOCKED·재시도·격리 검증(`test_task_queue.py`). 커넥션 둘로 동시 집기를 확인한다                 |
```

- [ ] **Step 6: `app/worker/__init__.py` 의 안전망 설명을 고친다**

마지막 문단을 바꾼다. `queue.send()` 실패로 식사가 갇히는 경우는 이제 없다.

```
네 번째는 이제 성격이 다르다. 작업 등록이 도메인 커밋과 같은 트랜잭션이라
"식사는 들어갔는데 작업이 없는" 상태는 만들어지지 않는다. 남는 것은 작업이
3회 실패해 FAILED 로 격리된 경우다 — 그 식사가 ANALYZING 에 갇혀 있으므로 줍는다.
```

`loop.py       큐 폴링 루프. 삭제 시점이 여기 있다` → `loop.py       큐 폴링 루프. 커밋 시점이 여기 있다`

- [ ] **Step 7: 잔재가 남지 않았는지 확인한다**

Run: `grep -rn -i "elasticmq\|sqs\|boto3\|receipt\|visibility" backend/app backend/scripts backend/README.md backend/.env.example infra --include="*" | grep -v __pycache__`
Expected: 결과 없음. 설계 문서(`docs/`)에는 비교 서술로 남아 있어도 된다.

- [ ] **Step 8: 전체 테스트와 컨테이너 기동을 확인한다**

Run: `pytest`
Expected: 전부 통과. skip 0건 (ElasticMQ 조건부 skip 이 사라졌다)

Run: `cd ../infra; docker compose -f docker-compose.yml -f docker-compose.ai-stub.yml -f docker-compose.be.yml up -d --build; docker logs glp1-worker --tail 20`
Expected: 워커 로그에 `워커 시작. 큐를 폴링한다.` 가 찍히고 재시작 루프에 빠지지 않는다

- [ ] **Step 9: 커밋**

```bash
git add backend/README.md backend/.env.example backend/app/worker/__init__.py infra/README.md infra/docker-compose.be.yml
git commit -m "[BE] docs: 큐 문서와 컨테이너 설정을 DB 테이블 큐 기준으로 갱신"
```

---

## 완료 확인

- [ ] `pytest` 전부 통과, skip 0건
- [ ] `alembic upgrade head` → `downgrade -1` → `upgrade head` 가 깨지지 않는다
- [ ] `python -m scripts.smoke_queue_ai` 가 정상·실패 경로 둘 다 통과
- [ ] `grep -ri "elasticmq\|sqs\|boto3" backend infra` 가 비어 있다 (`__pycache__` 제외)
- [ ] `docker compose … up -d` 로 api·worker 가 뜨고 워커가 폴링 로그를 남긴다
