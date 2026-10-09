# 작업 큐 lease 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 워커가 AI 응답을 기다리는 동안 DB 트랜잭션·행 잠금·커넥션을 쥐지 않도록, `task_queue` 를 lease 방식(PROCESSING + 토큰 + 만료)으로 바꾸고 핸들러를 `load / call_ai / apply` 3단계로 쪼갠다.

**Architecture:** `claim` 이 SKIP LOCKED 로 집은 행을 `PROCESSING` 으로 바꿔 즉시 커밋한다. 워커 루프는 `read()`(롤백 전용) 세션으로 `load`, 세션 없이 `call_ai`, `complete(lease)` 세션으로 `apply` + DONE 을 한 트랜잭션에 커밋한다. 완료·실패·반납은 `lease_token` 이 자기 것일 때만 행을 바꾸고, 만료된 lease 는 다음 `claim` 이 회수한다.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x (sync, `autoflush=False`, `expire_on_commit=False`), PostgreSQL 17, Alembic, pytest + testcontainers.

**Spec:** `docs/superpowers/specs/2026-10-05-queue-lease-design.md`

## Global Constraints

- 브랜치: `be/refactor-queue-lease` (develop 에서 분기). push 는 사용자가 요청할 때만.
- 커밋 메시지: `[BE-5] <type>: <한국어 요약>` + 빈 줄 + `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
- 테스트 실행: `backend` 디렉터리에서 `.venv/Scripts/python -m pytest <경로> -q` (Docker Desktop 이 떠 있어야 한다 — testcontainers 가 Postgres 를 띄운다).
- 로그·`task_queue.result`·`last_error` 에 음식명·본문·이미지 키 등 민감정보를 남기지 않는다 (규칙 6). 기존 로그 문구의 필드 구성을 바꾸지 않는다.
- `QUEUE_LEASE_SEC = 120`, `QUEUE_MAX_ATTEMPTS = 3`, `QUEUE_BACKOFF_BASE_SEC = 30` (30s → 60s), `AI_TIMEOUT_SEC = 45`.
- `attempts` = **집은 횟수**(claim 때 +1). `ClaimedTask.attempts` = 이번 시도 **이전까지** 집힌 횟수(증분 전 값). 격리 판정은 증분 후 `attempts >= QUEUE_MAX_ATTEMPTS`.
- 완료·실패·반납 가드: `WHERE id = :id AND status = 'PROCESSING' AND lease_token = :mine`.
- job 의 도메인 로직(검증·재확인·upsert)은 바꾸지 않는다. 자르기만 한다.
- 주석·docstring 은 한국어, 주변 코드의 밀도와 말투(`~다.` 체)를 따른다.

## File Structure

| 파일 | 책임 | 작업 |
|---|---|---|
| `backend/app/models/enums.py` | `TaskStatus.PROCESSING`, `is_in_flight` | Task 1 |
| `backend/app/models/task.py` | `lease_token`, `lease_expires_at`, 부분 인덱스 | Task 1 |
| `backend/alembic/versions/20261005_1200_9a1c4e7b2d3f_add_task_queue_lease.py` | 스키마 마이그레이션 | Task 1 (생성) |
| `backend/app/infra/queue.py` | `QueueSettings.QUEUE_LEASE_SEC` (Task 1), lease API 전체 (Task 6) | Task 1, 6 |
| `backend/app/services/daily_feedback.py`, `backend/app/services/insight.py` | 진행 중 판정에 PROCESSING 포함 | Task 2 |
| `backend/app/worker/job.py` | `Job`, `Skip` — 3단계 계약 | Task 3 (생성) |
| `backend/app/worker/jobs/analyze_meal.py` | 3단계 분리 + `on_ai_error` | Task 3 |
| `backend/app/worker/jobs/feedback_meal.py` | 3단계 분리 | Task 4 |
| `backend/app/worker/jobs/feedback_daily.py`, `feedback_long.py` | 3단계 분리 (`call_ai` → None 경로) | Task 5 |
| `backend/app/worker/dispatch.py` | `type → Job` 매핑, `get_job` | Task 3(과도기), 6 |
| `backend/app/worker/loop.py` | `process_one` 3단계 실행, 실패·반납·lease 유실 분기 | Task 6 |
| `backend/app/worker_main.py` | lease ≤ AI 타임아웃이면 기동 거부 | Task 6 |
| `backend/app/tests/conftest.py` | 커밋하는 세션 팩토리 `sessions` 픽스처 이동 | Task 6 |
| `backend/scripts/smoke_queue_ai.py`, `backend/scripts/queue_status.py` | 새 API · PROCESSING 표시 | Task 6, 7 |
| `backend/README.md`, `infra/docker-compose.be.yml`, `backend/.env.example`, 옛 spec | 문서·운영 | Task 7 |

과도기: Task 3~5 동안 `dispatch.py` 의 `_HANDLERS` 는 `<module>.JOB.run_inline` 을 가리킨다 — 시그니처가 옛 `run(db, task, ai)` 와 같아서 옛 루프가 그대로 돈다. Task 6 에서 `get_job` 으로 바꾼다.

---

### Task 1: 스키마 — PROCESSING · lease 컬럼 · 설정

**Files:**
- Modify: `backend/app/models/enums.py:118-130`
- Modify: `backend/app/models/task.py`
- Create: `backend/alembic/versions/20261005_1200_9a1c4e7b2d3f_add_task_queue_lease.py`
- Modify: `backend/app/infra/queue.py:36-57` (`QueueSettings`)
- Test: `backend/app/tests/test_task_queue.py`

**Interfaces:**
- Produces: `TaskStatus.PROCESSING`; `TaskStatus.is_in_flight -> bool` (property); `Task.lease_token: uuid.UUID | None`; `Task.lease_expires_at: datetime | None`; `QueueSettings.QUEUE_LEASE_SEC: int = 120`

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/app/tests/test_task_queue.py` 의 `test_new_task_defaults_to_pending_and_runnable_now` 바로 아래에 추가하고, 파일 상단 import 에 `import uuid` 를 더한다.

```python
def test_new_task_has_no_lease(sessions):
    """lease 는 claim 이 쓴다. 막 넣은 작업에는 없다."""
    with sessions() as db:
        task = Task(type="meal.analyze", payload={"mealId": "m1"})
        db.add(task)
        db.commit()
        db.refresh(task)

        assert task.lease_token is None
        assert task.lease_expires_at is None


def test_processing_and_lease_columns_round_trip(sessions):
    token = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(minutes=2)
    with sessions() as db:
        task = Task(
            type="meal.analyze",
            payload={"mealId": "m1"},
            status=TaskStatus.PROCESSING,
            lease_token=token,
            lease_expires_at=expires,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    with sessions() as db:
        row = _row(db, task_id)
        assert row.status is TaskStatus.PROCESSING
        assert row.lease_token == token
        assert row.lease_expires_at == expires


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TaskStatus.PENDING, True),
        (TaskStatus.PROCESSING, True),
        (TaskStatus.DONE, False),
        (TaskStatus.FAILED, False),
    ],
)
def test_in_flight_means_not_finished(status, expected):
    """"생성 중" 판정과 중복 등록 방지는 대기 중과 처리 중을 똑같이 본다."""
    assert status.is_in_flight is expected


def test_lease_defaults_to_two_minutes():
    from app.infra.queue import QueueSettings

    assert QueueSettings().QUEUE_LEASE_SEC == 120
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_task_queue.py -q -k "lease or in_flight"`
Expected: FAIL — `AttributeError: PROCESSING` / `TypeError: 'lease_token' is an invalid keyword argument` / `QUEUE_LEASE_SEC` 없음

- [ ] **Step 3: `TaskStatus` 수정** — `backend/app/models/enums.py` 의 `class TaskStatus` 전체를 아래로 바꾼다.

```python
class TaskStatus(str, enum.Enum):
    """`task_queue` 행의 상태.

    PROCESSING 은 워커가 lease 를 빌려 처리 중이라는 뜻이다. `claim` 이 집는 순간
    커밋하므로 다른 세션에도 보인다. `lease_expires_at` 이 지나도 PROCESSING 이면 워커가
    죽은 것으로 보고 다음 `claim` 이 회수해 PENDING(상한이면 FAILED)으로 돌린다.

    FAILED 는 DLQ 자리다. QUEUE_MAX_ATTEMPTS 만큼 집혔는데 끝내지 못하면 여기로 옮기고
    더 집지 않는다.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"

    @property
    def is_in_flight(self) -> bool:
        """아직 끝나지 않았다 — 대기 중이거나 처리 중이다.

        "생성 중" 응답과 중복 등록 방지가 이걸 본다. PENDING 만 보면 워커가 집은 순간
        (PROCESSING) 생성 중 표시가 사라지고 새로고침이 같은 작업을 또 넣는다.
        """
        return self in (TaskStatus.PENDING, TaskStatus.PROCESSING)
```

- [ ] **Step 4: `Task` 모델에 컬럼·인덱스 추가** — `backend/app/models/task.py`

import 줄을 `from sqlalchemy import DateTime, Index, Integer, Text, Uuid, func, text` 로 바꾸고, `attempts` 컬럼 docstring 을 `"""지금까지 **집힌** 횟수. claim 이 1 올린다. 첫 시도 전에는 0 이다."""` 로 바꾼다. `finished_at` 아래에 추가:

```python
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    """claim 할 때마다 새로 발급한다. 완료·실패·반납은 이 값이 자기 것일 때만 행을 바꾼다."""
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """이 시각이 지나도 PROCESSING 이면 다른 워커가 회수한다. PROCESSING 이 아니면 NULL 이다."""
```

`__table_args__` 튜플의 `ix_task_queue_pending` 다음에 추가:

```python
        # 만료된 lease 회수(`DbTaskQueue._reclaim_expired`) 전용. PROCESSING 은 워커 수만큼만
        # 있으므로 이 인덱스는 늘 작다.
        Index(
            "ix_task_queue_processing",
            "lease_expires_at",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
```

모듈 docstring 의 "워커는 SELECT … FOR UPDATE SKIP LOCKED 로 한 행을 집고, **처리하는 동안 잠금을 유지한다.** 그래서 실패하면 롤백만으로 작업이 되돌아간다 — 재배달 타이머가 따로 없다." 두 문장을 아래로 바꾼다:

```
워커는 SELECT … FOR UPDATE SKIP LOCKED 로 한 행을 집어 **lease 로 빌린다** — PROCESSING ·
토큰 · 만료 시각을 쓰고 곧바로 커밋한다. AI 를 기다리는 동안 트랜잭션을 쥐지 않는다.

설계 배경은 docs/superpowers/specs/2026-10-05-queue-lease-design.md 에 있다.
```

(기존 마지막 줄 `설계 배경은 …2026-09-20-db-table-queue-design.md 에 있다.` 는 지운다.)

- [ ] **Step 5: 마이그레이션 생성** — `backend/alembic/versions/20261005_1200_9a1c4e7b2d3f_add_task_queue_lease.py`

```python
"""add task_queue lease

Revision ID: 9a1c4e7b2d3f
Revises: 05ef77bf2a6a
Create Date: 2026-10-05 12:00:00.000000

워커가 AI 를 기다리는 동안 트랜잭션·행 잠금을 쥐지 않도록 lease 방식으로 바꾼다
(docs/superpowers/specs/2026-10-05-queue-lease-design.md). 지금 구조에서는 PROCESSING
행이 존재할 수 없으므로 데이터 이전은 없다.

**배포 순서**: 이 마이그레이션 → 워커 교체. 옛 워커는 lease 를 모른다 — 새 워커와
동시에 돌리지 않는다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9a1c4e7b2d3f'
down_revision: Union[str, Sequence[str], None] = '05ef77bf2a6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 새 ENUM 값은 그 값을 추가한 트랜잭션 안에서 쓸 수 없다("unsafe use of new value").
    # 바로 아래 부분 인덱스의 WHERE 가 이 값을 쓰므로 먼저 따로 커밋한다.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'PROCESSING' AFTER 'PENDING'")

    op.add_column('task_queue', sa.Column('lease_token', sa.Uuid(), nullable=True))
    op.add_column(
        'task_queue',
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_task_queue_processing',
        'task_queue',
        ['lease_expires_at'],
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )


def downgrade() -> None:
    """Downgrade schema.

    ENUM 값은 Postgres 가 지울 수 없어 남긴다. 쓰는 행만 PENDING 으로 되돌린다.
    """
    op.execute("UPDATE task_queue SET status = 'PENDING' WHERE status = 'PROCESSING'")
    op.drop_index('ix_task_queue_processing', table_name='task_queue')
    op.drop_column('task_queue', 'lease_expires_at')
    op.drop_column('task_queue', 'lease_token')
```

- [ ] **Step 6: `QueueSettings` 에 lease 길이 추가** — `backend/app/infra/queue.py` 의 `QUEUE_BACKOFF_BASE_SEC` docstring 바로 아래(아직 `QUEUE_IDLE_TX_TIMEOUT_SEC` 는 지우지 않는다 — Task 6 에서 지운다):

```python
    QUEUE_LEASE_SEC: int = 120
    """lease 길이. 이 안에 끝내지 못하면 다른 워커가 회수한다. AI 타임아웃(45초)보다
    넉넉히 길어야 정상 작업을 두 번 돌리지 않는다 — 워커가 기동할 때 확인한다.
    AI_TIMEOUT_SEC 는 httpx 의 단계별 타임아웃이라 총 시간 상한이 아니어서 두 배 넘게 둔다."""
```

- [ ] **Step 7: 통과 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_task_queue.py -q`
Expected: 전부 PASS (기존 테스트 포함 — 옛 claim 경로는 아직 그대로다)

- [ ] **Step 8: 커밋**

```bash
git add backend/app/models/enums.py backend/app/models/task.py backend/app/infra/queue.py \
  backend/alembic/versions/20261005_1200_9a1c4e7b2d3f_add_task_queue_lease.py \
  backend/app/tests/test_task_queue.py
git commit -m "[BE-5] feat: task_queue 에 PROCESSING 상태와 lease 컬럼 추가" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 서비스 — 처리 중도 "생성 중" 으로

**Files:**
- Modify: `backend/app/services/daily_feedback.py:67,135`
- Modify: `backend/app/services/insight.py:65,207`
- Test: `backend/app/tests/test_insight_service.py`, `backend/app/tests/test_daily_feedback_get_api.py`, `backend/app/tests/test_daily_feedback_refresh_api.py`

**Interfaces:**
- Consumes: `TaskStatus.is_in_flight` (Task 1)

- [ ] **Step 1: 실패하는 테스트 작성**

`backend/app/tests/test_insight_service.py` — `test_refresh_skips_enqueue_when_already_pending` 바로 아래에 추가:

```python
def test_processing_task_counts_as_generating():
    """워커가 집은 순간(PROCESSING) 생성 중 표시가 사라지면 FE 폴링이 낡은 행을 READY 로 본다."""
    assert _determine_status(_feedback_row(), _task(TaskStatus.PROCESSING)) == FeedbackStatus.GENERATING
    assert _determine_status(None, _task(TaskStatus.PROCESSING)) == FeedbackStatus.GENERATING


def test_refresh_skips_enqueue_when_already_processing(monkeypatch):
    """처리 중인 작업이 있어도 새로 넣지 않는다 — PENDING 만 보면 집힌 뒤의 연타가 중복으로 쌓인다."""
    monkeypatch.setattr(
        insight_crud, "get_latest_refresh_task", lambda *a, **k: _task(TaskStatus.PROCESSING)
    )
    enqueue_calls = []
    monkeypatch.setattr(
        insight_service, "enqueue", lambda db, task_type, payload: enqueue_calls.append(payload)
    )
    fake_db = MagicMock()

    result = refresh_long_term_insight(
        fake_db, user_id=uuid.uuid4(), period="7d", today=date(2026, 9, 23)
    )

    assert enqueue_calls == []
    fake_db.commit.assert_not_called()
    assert result.feedback_status == FeedbackStatus.GENERATING
```

`backend/app/tests/test_daily_feedback_get_api.py` — `test_05_no_row_with_pending_task_returns_generating` 바로 아래에 추가:

```python
def test_05b_no_row_with_processing_task_returns_generating(client, db):
    """#5 의 짝: 워커가 집어 처리 중(PROCESSING)이어도 GENERATING 이다."""
    user = make_user(db)
    _put_task(db, user_id=user.id, status=TaskStatus.PROCESSING)

    data = _data(_get(client, user))

    assert data["feedbackStatus"] == "GENERATING"
    assert data["dailyFeedbackId"] is None
```

`backend/app/tests/test_daily_feedback_refresh_api.py` — `test_latest_pending_blocks_even_with_older_done` 바로 위에 추가:

```python
def test_processing_task_blocks_new_enqueue_and_returns_generating(client, db):
    """처리 중인 작업이 있으면 새로 넣지 않는다 — PENDING 만 보면 집힌 뒤의 연타가 쌓인다."""
    user = make_user(db)
    _put_task(db, user_id=user.id, status=TaskStatus.PROCESSING)

    response = client.post(URL, json={"date": D}, headers=_headers(user))

    assert response.status_code == 202, response.text
    assert response.json()["data"]["feedbackStatus"] == "GENERATING"
    assert _count_tasks(db) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_insight_service.py app/tests/test_daily_feedback_get_api.py app/tests/test_daily_feedback_refresh_api.py -q -k "processing"`
Expected: 4 FAIL — GENERATING 대신 READY/PENDING, enqueue 가 호출됨, task 2개

- [ ] **Step 3: 구현** — 네 곳의 조건을 바꾼다.

`backend/app/services/daily_feedback.py` 67행과 135행, `backend/app/services/insight.py` 65행과 207행:

```python
    if latest_task is not None and latest_task.status == TaskStatus.PENDING:
```
→
```python
    if latest_task is not None and latest_task.status.is_in_flight:
```

`insight.py` `_determine_status` docstring 의 "대기 중인 작업이 있으면(갱신 중)" → "대기·처리 중인 작업이 있으면(갱신 중)", `daily_feedback.py` `_determine_status` docstring 의 "대기 중인 작업이 있으면(갱신 중)" → "대기·처리 중인 작업이 있으면(갱신 중)". `insight.py:197` docstring 의 "**이미 대기 중인 작업이 있으면 새로 넣지 않는다.**" → "**이미 대기·처리 중인 작업이 있으면 새로 넣지 않는다.**".

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_insight_service.py app/tests/test_daily_feedback_get_api.py app/tests/test_daily_feedback_refresh_api.py app/tests/test_daily_feedback_service.py -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add backend/app/services/daily_feedback.py backend/app/services/insight.py \
  backend/app/tests/test_insight_service.py backend/app/tests/test_daily_feedback_get_api.py \
  backend/app/tests/test_daily_feedback_refresh_api.py
git commit -m "[BE-5] fix: 처리 중(PROCESSING)인 피드백 작업도 생성 중으로 보고 중복 등록을 막는다" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `Job` 계약 + `meal.analyze` 3단계 분리

**Files:**
- Create: `backend/app/worker/job.py`
- Modify: `backend/app/worker/jobs/analyze_meal.py` (`run` · `_fail_or_raise` 주변)
- Modify: `backend/app/worker/dispatch.py:49-54` (`_HANDLERS` 한 줄)
- Test: `backend/app/tests/test_analyze_meal_job.py`

**Interfaces:**
- Consumes: `ClaimedTask`, `NonRetryableError` (`app.infra.queue`), `AiClient` (`app.infra.ai`)
- Produces:
  - `app.worker.job.Skip(result: dict[str, Any] | None = None)` — frozen dataclass
  - `app.worker.job.Job(load, call_ai, apply, on_ai_error=None)` — frozen dataclass
    - `load: Callable[[Session, ClaimedTask], Any]` (ctx 또는 `Skip`)
    - `call_ai: Callable[[Any, AiClient], Any]` (응답 또는 None)
    - `apply: Callable[[Session, ClaimedTask, Any, Any], dict[str, Any] | None]`
    - `on_ai_error: Callable[[Session, ClaimedTask, Any, Exception], dict[str, Any] | None] | None`
    - `Job.run_inline(db: Session, task: ClaimedTask, ai: AiClient) -> dict[str, Any] | None`
  - `app.worker.jobs.analyze_meal.JOB: Job`

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/app/tests/test_analyze_meal_job.py`

28행 `from app.worker.jobs.analyze_meal import run` 을 지우고, `LAST_ATTEMPT = …` 바로 아래에 둔다:

```python
# 세 단계를 세션 하나로 이어 돈다. 시나리오 단언은 3단계 분리 전과 같다 — 동작이 바뀌지
# 않았다는 증거다.
run = analyze_meal.JOB.run_inline
```

파일 맨 끝에 추가:

```python
# ─────────────────────────── 3단계 계약 ───────────────────────────


def test_load_hands_over_plain_data_not_orm_objects(db, storage):
    """`load` 의 세션은 큐가 닫는다. ctx 에 ORM 객체가 실리면 `apply` 에서 못 쓴다."""
    from app.db.base import Base

    meal = _analyzing_meal(db)

    ctx = analyze_meal.load(db, _task(meal))

    assert ctx.meal_id == meal.id
    assert not any(isinstance(value, Base) for value in vars(ctx).values())


def test_load_skips_without_calling_ai(db, storage):
    from app.worker.job import Skip

    meal = _analyzing_meal(db)
    meal.status = MealStatus.REVIEW_REQUIRED
    db.flush()

    assert analyze_meal.load(db, _task(meal)) == Skip(None)


def test_ai_error_on_last_attempt_skips_a_meal_deleted_meanwhile(db, storage):
    """AI 가 끝내 실패했는데 그사이 사용자가 지웠으면 FAILED 로 덮지 않는다."""
    meal = _analyzing_meal(db)
    ctx = analyze_meal.load(db, _task(meal))
    db.execute(update(Meal).where(Meal.id == meal.id).values(deleted_at=datetime.now(UTC)))

    result = analyze_meal.on_ai_error(db, _task(meal, attempts=LAST_ATTEMPT), ctx, RuntimeError("AI 다운"))

    assert result is None
```

(`_analyzing_meal` · `_task` 는 이 파일에 이미 있다. `lock_meal_for_worker` 는 `populate_existing` 으로 다시 읽으므로 Core UPDATE 뒤에 `expire_all` 이 필요 없다.)

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_analyze_meal_job.py -q`
Expected: FAIL — `AttributeError: module 'app.worker.jobs.analyze_meal' has no attribute 'JOB'` (수집 단계에서 전부)

- [ ] **Step 3: `backend/app/worker/job.py` 생성**

```python
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
```

- [ ] **Step 4: `analyze_meal.py` 를 3단계로 자른다**

import 에 `from app.worker.job import Job, Skip` 를 더한다.

`_Recognized` dataclass 바로 아래에 추가:

```python
@dataclass(frozen=True)
class _Ctx:
    """`load` 가 넘기는 값. ORM 객체를 싣지 않는다 — `load` 의 세션은 곧 닫힌다."""

    meal_id: uuid.UUID
    body: dict[str, Any]


@dataclass(frozen=True)
class _Analysis:
    """검증을 마친 AI 응답."""

    safety_status: str
    items: list[_Recognized]
```

`def run(...)` 함수 전체를 지우고 그 자리에 아래를 둔다. 지우기 전에 `run` docstring 의 `순서:` 블록(1~9)과 그 아래 두 문단("**쓰기 직전에 식사를 잠그고 다시 읽는다** …", "🔗 TODO(범위 밖): …")을 **글자 그대로** 모듈 docstring 끝(`BE ↔ AI 계약은 …` 문단 다음)으로 옮기고, `순서:` 줄을 `순서 (load 1~2 · call_ai 3 · on_ai_error 4 · apply 5~9):` 로 바꾼다.

```python
def load(db: Session, task: ClaimedTask) -> _Ctx | Skip:
    """식사를 읽는다. 행이 없으면 raise, 건너뛸 식사면 Skip (모듈 docstring 순서 1~2)."""
    body = task.payload
    meal = meal_crud.get_meal_for_worker(db, uuid.UUID(body["mealId"]))
    if meal is None:
        raise ValueError(f"식사를 찾을 수 없다: {body['mealId']}")
    if _should_skip(meal):
        return Skip(None)
    return _Ctx(meal_id=meal.id, body=body)


def call_ai(ctx: _Ctx, ai: AiClient) -> _Analysis:
    """AI 호출 + 응답 검증 (순서 3). 계약과 다르면 raise — AI 실패로 다룬다.

    presigned URL 은 `_request` 가 여기서(호출 직전에) 새로 발급한다.
    """
    response = ai.analyze_meal(_request(ctx.body))
    model_version = response["modelVersion"]
    safety_status = response["safetyStatus"]
    return _Analysis(
        safety_status=safety_status,
        items=[
            _parse_item(raw, model_version=model_version, safety_status=safety_status)
            for raw in response["items"]
        ],
    )


def apply(db: Session, task: ClaimedTask, ctx: _Ctx, analysis: _Analysis) -> dict[str, Any] | None:
    """식사를 잠그고 다시 본 뒤 쓴다 (순서 5~9)."""
    meal = meal_crud.lock_meal_for_worker(db, ctx.meal_id)
    if meal is None or _should_skip(meal):
        return None

    if not analysis.items:
        return _finish(db, meal, MealStatus.FAILED, item_count=0)

    try:
        with db.begin_nested():
            _replace_model_items(db, meal, analysis.items)
    except SQLAlchemyError as exc:
        return _fail_or_raise(db, meal, task, exc)

    # 커밋하지 않는다. 큐가 DONE 과 함께 한 번에 커밋한다(`queue.complete`). 여기서 터지면
    # 도메인 변경까지 통째로 롤백된다.

    # 포즈 정보는 민감 건강정보다. 음식명·이미지 키를 로그에 남기지 않는다(규칙 6).
    logger.info(
        "분석 완료 mealId=%s items=%d safetyStatus=%s",
        meal.id,
        len(analysis.items),
        analysis.safety_status,
    )
    return _finish(db, meal, MealStatus.REVIEW_REQUIRED, item_count=len(analysis.items))


def on_ai_error(db: Session, task: ClaimedTask, ctx: _Ctx, exc: Exception) -> dict[str, Any] | None:
    """AI 가 실패했다 (순서 4). 마지막 시도 전이면 다시 올려 큐의 재시도에 맡기고, 마지막이면
    식사를 `FAILED` 로 둔다. 그사이 지워졌거나 분석 중이 아니게 된 식사는 건드리지 않는다."""
    meal = meal_crud.lock_meal_for_worker(db, ctx.meal_id)
    if meal is None or _should_skip(meal):
        return None
    return _fail_or_raise(db, meal, task, exc)
```

파일 맨 끝(`_finish` 아래)에 추가:

```python
JOB = Job(load=load, call_ai=call_ai, apply=apply, on_ai_error=on_ai_error)
```

`_fail_or_raise` · `_is_last_attempt` 는 그대로 둔다. `_is_last_attempt` docstring 의 "`task.attempts` 는 **이전까지** 실패한 횟수다 — 실패를 기록할 때 1 을 더한다." 를 "`task.attempts` 는 이번 시도 **이전까지** 집힌 횟수다 — 큐가 claim 할 때 1 을 더한다." 로 바꾼다.

- [ ] **Step 5: 과도기 dispatch** — `backend/app/worker/dispatch.py` 의 `_HANDLERS` 에서

```python
    "meal.analyze": analyze_meal.run,
```
→
```python
    "meal.analyze": analyze_meal.JOB.run_inline,
```

- [ ] **Step 6: 통과 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_analyze_meal_job.py app/tests/test_worker_loop.py -q`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add backend/app/worker/job.py backend/app/worker/jobs/analyze_meal.py \
  backend/app/worker/dispatch.py backend/app/tests/test_analyze_meal_job.py
git commit -m "[BE-5] refactor: 작업 핸들러 계약을 load·call_ai·apply 로 — meal.analyze 부터" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `feedback.meal` 3단계 분리

**Files:**
- Modify: `backend/app/worker/jobs/feedback_meal.py:157-244`
- Modify: `backend/app/worker/dispatch.py` (`_HANDLERS` 한 줄)
- Test: `backend/app/tests/test_feedback_meal_job.py`

**Interfaces:**
- Consumes: `Job`, `Skip` (Task 3)
- Produces: `app.worker.jobs.feedback_meal.JOB: Job`

- [ ] **Step 1: 실패하는 테스트 작성** — `backend/app/tests/test_feedback_meal_job.py`

38행 `from app.worker.jobs.feedback_meal import run` 를 아래로 바꾼다:

```python
from app.worker.jobs import feedback_meal

# 세 단계를 세션 하나로 이어 돈다. 시나리오 단언은 3단계 분리 전과 같다.
run = feedback_meal.JOB.run_inline
```

파일 맨 끝에 추가:

```python
# ─────────────────────────── 3단계 계약 ───────────────────────────


def test_load_hands_over_plain_data_not_orm_objects(db):
    from app.db.base import Base

    user = make_user(db)
    meal = _evaluated_meal(db, user)

    ctx = feedback_meal.load(db, _task(meal.id))

    assert ctx.meal_id == meal.id
    assert not any(isinstance(value, Base) for value in vars(ctx).values())
    assert ctx.request["scope"] == "MEAL"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_feedback_meal_job.py -q`
Expected: FAIL — `has no attribute 'JOB'`

- [ ] **Step 3: 구현** — `feedback_meal.py`

import 에 `from dataclasses import dataclass` 와 `from app.worker.job import Job, Skip` 를 더한다. `_scored_as` 아래에 추가:

```python
@dataclass(frozen=True)
class _Ctx:
    """`load` 가 넘기는 값. ORM 객체를 싣지 않는다 — `load` 의 세션은 곧 닫힌다."""

    meal_id: uuid.UUID
    scored_as: tuple[Any, ...]
    """AI 에 넘긴 채점. `apply` 가 쓰기 직전에 이게 그대로인지 본다."""
    request: dict[str, Any]
```

`def run(...)` 전체를 아래로 바꾼다. `run` docstring 의 `순서:` 블록(1~5)은 모듈 docstring 끝으로 글자 그대로 옮기고 `순서:` 줄을 `순서 (load 1~3 전반 · call_ai 3 · apply 4~5):` 로 바꾼다.

```python
def load(db: Session, task: ClaimedTask) -> _Ctx | Skip:
    """식사·평가를 읽고 AI 요청을 만든다. 평가가 아직 없으면 raise — 재시도에 맡긴다."""
    meal_id = uuid.UUID(task.payload["mealId"])

    meal = meal_crud.get_meal_for_worker(db, meal_id)
    if (reason := _skip_reason(meal)) is not None:
        logger.info("끼니 피드백 건너뜀 mealId=%s reason=%s", meal_id, reason)
        return Skip({"skipped": reason})

    evaluation = evaluation_crud.get_by_meal(db, meal.id)
    if evaluation is None:
        raise LookupError(f"meal {meal_id} 의 Q/Q/S 평가가 아직 없다")

    return _Ctx(
        meal_id=meal.id,
        scored_as=_scored_as(evaluation),
        request={
            "scope": "MEAL",
            "userId": str(meal.user_id),
            # 채점 시점의 단계다. 점수와 같은 기준으로 문장을 써야 한다.
            "stage": evaluation.stage_at_evaluation.value,
            # 채점 못 한 축은 null 이다 — 0 으로 채우면 "못 쟀다" 가 "바닥이다" 가 된다.
            "qqs": {
                "quantity": _number(evaluation.quantity_score),
                "quality": _number(evaluation.quality_score),
                "satiety": _number(evaluation.satiety_score),
            },
            "mealId": str(meal.id),
            "items": [_item_payload(db, item) for item in meal.items],
            "satiety": _satiety_payload(db, meal.id),
        },
    )


def call_ai(ctx: _Ctx, ai: AiClient) -> dict[str, Any]:
    return ai.short_feedback(ctx.request)


def apply(db: Session, task: ClaimedTask, ctx: _Ctx, result: dict[str, Any]) -> dict[str, Any]:
    """식사를 잠그고 다시 본 뒤 upsert 한다."""
    # AI 를 부르는 동안(최대 45초) 잠그지 않았다 — 그 사이 사용자가 지우거나 고쳤을 수 있다.
    # 워커 루프에서는 새 세션이라 identity map 이 비어 있지만, `JOB.run_inline` 은 `load` 와
    # 같은 세션을 쓴다. 그래서 다시 읽기 전에 비운다. 객체 하나가 아니라 전부다 — 그 사이 평가
    # 행이 지워졌으면 옛 객체는 이미 세션에서 떨어져 있을 수 있다.
    db.expire_all()
    meal = meal_crud.lock_meal_for_worker(db, ctx.meal_id)
    if (reason := _skip_reason(meal)) is not None:
        logger.info("끼니 피드백 건너뜀 mealId=%s reason=%s", ctx.meal_id, reason)
        return {"skipped": reason}

    # 상태만으로는 부족하다 — 포만감만 고쳐 재확정하면 EVALUATED 그대로다. 그 재확정이
    # 넣은 작업이 다른 워커에서 먼저 끝날 수 있어서, 여기서 쓰면 옛 점수로 쓴 문장이
    # 새 문장을 덮고 영영 남는다. 확정이 식사 행을 먼저 잠그므로(`services/evaluation`)
    # 잠금을 얻은 지금 읽는 평가는 커밋이 끝난 값이다.
    current = evaluation_crud.get_by_meal(db, meal.id)
    if current is None or _scored_as(current) != ctx.scored_as:
        logger.info("끼니 피드백 건너뜀 mealId=%s reason=EVALUATION_CHANGED", ctx.meal_id)
        return {"skipped": "EVALUATION_CHANGED"}

    suggestions = _stored_suggestions(db, result.get("suggestions"))
    # safetyStatus 는 AI 가 준 그대로 저장한다. SAFE 로 올리지 않는다 (규칙 1).
    meal_feedback_id = feedback_crud.upsert(
        db,
        user_id=meal.user_id,
        meal_id=meal.id,
        body=result["body"],
        reasoning=result.get("reasoning"),
        suggestions=suggestions,
        model_version=result["modelVersion"],
        safety_status=SafetyStatus(result["safetyStatus"]),
    )

    # 커밋하지 않는다. 큐가 DONE 과 함께 한 번에 커밋한다(`queue.complete`).

    # 로그·반환값(task_queue.result)에는 식별자와 개수만 — 음식명·본문·제안 문구는
    # 남기지 않는다 (규칙 6).
    logger.info(
        "끼니 피드백 완료 mealId=%s safetyStatus=%s",
        ctx.meal_id,
        result["safetyStatus"],
    )

    return {
        "mealFeedbackId": str(meal_feedback_id),
        "suggestionCount": len(suggestions or []),
    }


JOB = Job(load=load, call_ai=call_ai, apply=apply)
```

- [ ] **Step 4: 과도기 dispatch** — `"feedback.meal": feedback_meal.run,` → `"feedback.meal": feedback_meal.JOB.run_inline,`

- [ ] **Step 5: 통과 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_feedback_meal_job.py app/tests/test_worker_loop.py -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/app/worker/jobs/feedback_meal.py backend/app/worker/dispatch.py backend/app/tests/test_feedback_meal_job.py
git commit -m "[BE-5] refactor: feedback.meal 을 load·call_ai·apply 로 나눈다" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `feedback.daily` · `feedback.long` 3단계 분리

**Files:**
- Modify: `backend/app/worker/jobs/feedback_daily.py:46-136`
- Modify: `backend/app/worker/jobs/feedback_long.py:40-157`
- Modify: `backend/app/worker/dispatch.py` (`_HANDLERS` 두 줄)
- Test: `backend/app/tests/test_feedback_daily_job.py`, `backend/app/tests/test_feedback_long_job.py`

**Interfaces:**
- Consumes: `Job` (Task 3)
- Produces: `feedback_daily.JOB: Job`, `feedback_long.JOB: Job`. 두 job 의 `call_ai` 는 근거·데이터가 모자라면 `None` 을 돌려주고, `apply` 는 `None` 이면 같은 키의 행을 지운다.

- [ ] **Step 1: 실패하는 테스트 작성**

`test_feedback_daily_job.py` 33행 `from app.worker.jobs.feedback_daily import run` →

```python
from app.worker.jobs import feedback_daily

# 세 단계를 세션 하나로 이어 돈다. 시나리오 단언은 3단계 분리 전과 같다.
run = feedback_daily.JOB.run_inline
```

`test_feedback_long_job.py` 41행 `from app.worker.jobs.feedback_long import run` →

```python
from app.worker.jobs import feedback_long

# 세 단계를 세션 하나로 이어 돈다. 시나리오 단언은 3단계 분리 전과 같다.
run = feedback_long.JOB.run_inline
```

`test_feedback_daily_job.py` 맨 끝에 추가:

```python
# ─────────────────────────── 3단계 계약 ───────────────────────────


def test_no_evidence_means_no_ai_call_and_no_request(db):
    """근거가 없으면 `call_ai` 가 AI 를 부르지 않고 None — `apply` 가 그날 행을 지운다."""
    user = make_user(db)
    ctx = feedback_daily.load(db, _task(user.id))

    assert ctx.request is None
    assert feedback_daily.call_ai(ctx, FakeAi()) is None
```

`test_feedback_long_job.py` 맨 끝에 추가:

```python
# ─────────────────────────── 3단계 계약 ───────────────────────────


def test_insufficient_days_mean_no_ai_call(db):
    user = make_user(db)
    ctx = feedback_long.load(db, _task(user.id))

    assert ctx.request is None
    assert feedback_long.call_ai(ctx, FakeAi()) is None
```

(`_task` · `FakeAi` · `make_user` 는 두 파일에 이미 있다. `FakeAi()` 가 호출되면 기록만 하므로 호출되지 않았음은 반환값 None 으로 충분하다.)

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_feedback_daily_job.py app/tests/test_feedback_long_job.py -q`
Expected: FAIL — `has no attribute 'JOB'`

- [ ] **Step 3: `feedback_daily.py` 구현**

import 에 `from dataclasses import dataclass` 와 `from app.worker.job import Job` 를 더한다. `_average` 아래에 추가하고, `def run(...)` 전체를 아래 세 함수 + `JOB` 으로 바꾼다. `run` docstring 의 `순서:` 블록(1~5)은 모듈 docstring 끝으로 글자 그대로 옮기고 `순서:` 줄을 `순서 (load 1~3 · call_ai 4 · apply 5, 근거가 없으면 apply 가 1 의 삭제):` 로 바꾼다.

```python
@dataclass(frozen=True)
class _Ctx:
    """`load` 가 넘기는 값. ORM 객체를 싣지 않는다 — `load` 의 세션은 곧 닫힌다."""

    user_id: uuid.UUID
    feedback_date: date
    qqs: dict[str, int] | None
    meal_feedback_ids: list[uuid.UUID]
    request: dict[str, Any] | None
    """None 이면 근거가 없다 — AI 를 부르지 않고 `apply` 가 그날 행을 지운다."""


def load(db: Session, task: ClaimedTask) -> _Ctx:
    body = task.payload
    user_id = uuid.UUID(body["userId"])
    feedback_date = date.fromisoformat(body["date"])
    range_start, range_end = _kst_day_range(feedback_date)

    evidence = daily_feedback_crud.list_day_evidence(
        db, user_id=user_id, range_start=range_start, range_end=range_end
    )
    if not evidence:
        return _Ctx(
            user_id=user_id, feedback_date=feedback_date, qqs=None, meal_feedback_ids=[], request=None
        )

    # AI payload 는 JSON 으로 나간다 — Decimal·UUID·date 객체를 싣지 않는다.
    meals = [
        {
            "mealType": row.meal_type.value,
            "summary": row.body,
            "qqs": {
                "quantity": float(row.quantity_score),
                "quality": float(row.quality_score),
                "satiety": float(row.satiety_score),
            },
        }
        for row in evidence
    ]
    qqs = {
        "quantity": _average([row.quantity_score for row in evidence]),
        "quality": _average([row.quality_score for row in evidence]),
        "satiety": _average([row.satiety_score for row in evidence]),
    }

    # 그날 마지막 식사 시점의 스냅샷 단계. 근거가 있으면 그날 식사도 있다.
    stages = meal_crud.get_day_stages(
        db, user_id=user_id, range_start=range_start, range_end=range_end
    )
    stage = stages[-1].stage

    return _Ctx(
        user_id=user_id,
        feedback_date=feedback_date,
        qqs=qqs,
        meal_feedback_ids=[row.meal_feedback_id for row in evidence],
        request={
            "scope": "DAILY",
            "userId": str(user_id),
            "stage": stage.value,
            "qqs": qqs,
            "date": feedback_date.isoformat(),
            "meals": meals,
        },
    )


def call_ai(ctx: _Ctx, ai: AiClient) -> dict[str, Any] | None:
    if ctx.request is None:
        return None
    return ai.short_feedback(ctx.request)


def apply(db: Session, task: ClaimedTask, ctx: _Ctx, result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        # 삭제된 식사로 만든 낡은 요약을 남기지 않는다.
        daily_feedback_crud.delete_for_day(db, user_id=ctx.user_id, feedback_date=ctx.feedback_date)
        logger.info("일일 피드백 근거 없음 userId=%s date=%s", ctx.user_id, ctx.feedback_date)
        return None

    # scope=DAILY 면 suggestions 는 null 이다 — 저장할 곳도 없다.
    # safetyStatus 는 AI 가 준 그대로 저장한다. SAFE 로 올리지 않는다 (규칙 1).
    daily_feedback_id = daily_feedback_crud.upsert(
        db,
        user_id=ctx.user_id,
        feedback_date=ctx.feedback_date,
        summary=result["body"],
        quantity_score=ctx.qqs["quantity"],
        quality_score=ctx.qqs["quality"],
        satiety_score=ctx.qqs["satiety"],
        model_version=result["modelVersion"],
        safety_status=SafetyStatus(result["safetyStatus"]),
    )
    daily_feedback_crud.replace_sources(
        db,
        daily_feedback_id=daily_feedback_id,
        meal_feedback_ids=ctx.meal_feedback_ids,
    )

    # 커밋하지 않는다. 큐가 DONE 과 함께 한 번에 커밋한다(`queue.complete`).

    # 로그·반환값(task_queue.result)에는 식별자와 개수만 — 본문·음식명은 남기지 않는다 (규칙 6).
    logger.info(
        "일일 피드백 완료 userId=%s date=%s safetyStatus=%s",
        ctx.user_id,
        ctx.feedback_date,
        result["safetyStatus"],
    )

    return {"dailyFeedbackId": str(daily_feedback_id), "sourceCount": len(ctx.meal_feedback_ids)}


JOB = Job(load=load, call_ai=call_ai, apply=apply)
```

- [ ] **Step 4: `feedback_long.py` 구현**

import 에 `from dataclasses import dataclass` 와 `from app.worker.job import Job` 를 더한다. `MIN_SCORED_DAYS` 정의 아래에 `_Ctx` 를 두고, `def run(...)` 전체를 아래로 바꾼다. `run` docstring 의 `순서:` 블록(1~5)은 모듈 docstring 끝으로 글자 그대로 옮기고 `순서:` 줄을 `순서 (load 1~3 · call_ai 4 · apply 5, 데이터가 모자라면 apply 가 2 의 삭제):` 로 바꾼다.

```python
@dataclass(frozen=True)
class _Ctx:
    """`load` 가 넘기는 값. ORM 객체를 싣지 않는다 — `load` 의 세션은 곧 닫힌다."""

    user_id: uuid.UUID
    period_type: FeedbackPeriodType
    period_start: date
    period_end: date
    day_count: int
    """점수 있는 날 수. 모자랄 때 로그에 남긴다."""
    series: list[dict[str, Any]]
    daily_feedback_ids: list[uuid.UUID]
    request: dict[str, Any] | None
    """None 이면 점수 있는 날이 `MIN_SCORED_DAYS` 미만이다 — AI 를 부르지 않고 `apply` 가 행을 지운다."""


def load(db: Session, task: ClaimedTask) -> _Ctx:
    body = task.payload
    user_id = uuid.UUID(body["userId"])
    period_type = FeedbackPeriodType(body["periodType"])
    period_start = date.fromisoformat(body["periodStart"])
    period_end = date.fromisoformat(body["periodEnd"])

    date_from = None if period_type is FeedbackPeriodType.ALL else period_start
    # [date_from, period_end] 양끝 포함 KST 날짜 → [start, end). eaten_at 은 UTC 로 저장된다.
    range_start = kst_day_range(date_from)[0] if date_from is not None else None
    range_end = kst_day_range(period_end)[1]

    # dashboard 와 같은 집계·반올림 — 차트 숫자와 AI 가 본 숫자가 같아야 한다.
    # 평균이 NULL 인 축이 있는 날은 뺀다. AI 계약(SeriesPoint)은 세 값을 다 요구한다.
    daily_scores = [
        row
        for row in dashboard_crud.get_daily_scores(
            db, user_id=user_id, range_start=range_start, range_end=range_end
        )
        if None not in (row.avg_quantity, row.avg_quality, row.avg_satiety)
    ]

    if len(daily_scores) < MIN_SCORED_DAYS:
        return _Ctx(
            user_id=user_id,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            day_count=len(daily_scores),
            series=[],
            daily_feedback_ids=[],
            request=None,
        )

    # AI payload 는 JSON 으로 나간다 — Decimal·UUID·date 객체를 싣지 않는다.
    series = [
        {
            "date": row.day.date().isoformat(),
            "quantity": round(row.avg_quantity),
            "quality": round(row.avg_quality),
            "satiety": round(row.avg_satiety),
        }
        for row in daily_scores
    ]

    # TODO: ALL 은 series · dailySummaries 에 상한이 없어 오래 쓴 사용자는 요청이 커진다.
    #  허용 최대치를 AI 계약(LongFeedbackRequest)으로 정한 뒤 자른다.
    sources = long_term_feedback_crud.list_period_sources(
        db, user_id=user_id, date_from=date_from, date_to=period_end
    )

    # 점수 있는 마지막 날의 마지막 식사 스냅샷 단계. 그날 식사는 반드시 있다.
    last_start, last_end = kst_day_range(daily_scores[-1].day.date())
    stage = meal_crud.get_day_stages(
        db, user_id=user_id, range_start=last_start, range_end=last_end
    )[-1].stage

    # ALL 의 period_start 는 UNIQUE 키 고정값(1970-01-01)이지 실제 날짜가 아니다. 그대로 보내면
    # 모델이 문장에 그 날짜를 쓸 수 있어, AI 에는 실제 분석 시작일(점수 있는 첫 날)을 보낸다.
    # WEEKLY · MONTHLY 는 창 자체가 분석 구간이라 첫 며칠이 비어도 창 시작일을 보낸다.
    ai_period_start = series[0]["date"] if date_from is None else period_start.isoformat()

    return _Ctx(
        user_id=user_id,
        period_type=period_type,
        period_start=period_start,
        period_end=period_end,
        day_count=len(daily_scores),
        series=series,
        daily_feedback_ids=[row.daily_feedback_id for row in sources],
        request={
            "userId": str(user_id),
            "periodType": period_type.value,
            "periodStart": ai_period_start,
            "periodEnd": period_end.isoformat(),
            "stage": stage.value,
            "series": series,
            "dailySummaries": [row.summary for row in sources],
        },
    )


def call_ai(ctx: _Ctx, ai: AiClient) -> dict[str, Any] | None:
    if ctx.request is None:
        return None
    return ai.long_feedback(ctx.request)


def apply(db: Session, task: ClaimedTask, ctx: _Ctx, result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        long_term_feedback_crud.delete_for_period(
            db, user_id=ctx.user_id, period_type=ctx.period_type, period_start=ctx.period_start
        )
        logger.info(
            "장기 피드백 데이터 부족 userId=%s periodType=%s periodStart=%s dayCount=%s",
            ctx.user_id,
            ctx.period_type.value,
            ctx.period_start,
            ctx.day_count,
        )
        return None

    # safetyStatus 는 AI 가 준 그대로 저장한다. SAFE 로 올리지 않는다 (규칙 1).
    long_term_feedback_id = long_term_feedback_crud.upsert(
        db,
        user_id=ctx.user_id,
        period_type=ctx.period_type,
        period_start=ctx.period_start,
        period_end=ctx.period_end,
        trend_summary=result["trendSummary"],
        recommendation=result["recommendation"],
        chart_data={"series": ctx.series},
        model_version=result["modelVersion"],
        safety_status=SafetyStatus(result["safetyStatus"]),
    )
    long_term_feedback_crud.replace_sources(
        db,
        long_term_feedback_id=long_term_feedback_id,
        daily_feedback_ids=ctx.daily_feedback_ids,
    )

    # 커밋하지 않는다. 큐가 DONE 과 함께 한 번에 커밋한다(`queue.complete`).

    # 로그·반환값(task_queue.result)에는 식별자와 개수만 — 문장은 남기지 않는다 (규칙 6).
    logger.info(
        "장기 피드백 완료 userId=%s periodType=%s safetyStatus=%s",
        ctx.user_id,
        ctx.period_type.value,
        result["safetyStatus"],
    )

    return {
        "longTermFeedbackId": str(long_term_feedback_id),
        "dayCount": len(ctx.series),
        "sourceCount": len(ctx.daily_feedback_ids),
    }


JOB = Job(load=load, call_ai=call_ai, apply=apply)
```

- [ ] **Step 5: 과도기 dispatch** — `_HANDLERS` 의 두 줄을 `feedback_daily.JOB.run_inline`, `feedback_long.JOB.run_inline` 으로.

- [ ] **Step 6: 통과 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_feedback_daily_job.py app/tests/test_feedback_long_job.py app/tests/test_worker_loop.py -q`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add backend/app/worker/jobs/feedback_daily.py backend/app/worker/jobs/feedback_long.py \
  backend/app/worker/dispatch.py backend/app/tests/test_feedback_daily_job.py backend/app/tests/test_feedback_long_job.py
git commit -m "[BE-5] refactor: feedback.daily·feedback.long 을 load·call_ai·apply 로 나눈다" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: lease 큐 · 워커 루프 · dispatch 전환

이 태스크는 쪼갤 수 없다 — 큐의 `claim()` 모양이 바뀌는 순간 루프·스모크 스크립트가 함께 바뀌어야 테스트가 돈다.

**Files:**
- Modify (전면): `backend/app/infra/queue.py`
- Modify (전면): `backend/app/worker/loop.py`
- Modify: `backend/app/worker/dispatch.py`
- Modify: `backend/app/worker_main.py`
- Modify: `backend/scripts/smoke_queue_ai.py`
- Modify: `backend/app/tests/conftest.py` (픽스처 추가)
- Modify (전면): `backend/app/tests/test_task_queue.py` 의 "집기"·"실패"·"동시성" 절, `backend/app/tests/test_worker_loop.py`
- Modify: `backend/app/tests/test_analyze_meal_job.py:459`, `test_feedback_daily_job.py:761`, `test_feedback_long_job.py:709`, `test_feedback_meal_job.py:678` (`handle(` 호출)

**Interfaces:**
- Consumes: `Job`, `Skip` (Task 3), 각 `<job>.JOB` (Task 3~5), `QueueSettings.QUEUE_LEASE_SEC` (Task 1)
- Produces:
  - `app.infra.queue.Lease(task: ClaimedTask, token: uuid.UUID)` — frozen dataclass
  - `app.infra.queue.Completion(db: Session, result: dict | None = None)` — dataclass
  - `app.infra.queue.LeaseLostError(Exception)`
  - `DbTaskQueue.claim() -> Lease | None`
  - `DbTaskQueue.read() -> ContextManager[Session]` (항상 롤백)
  - `DbTaskQueue.complete(lease) -> ContextManager[Completion]`
  - `DbTaskQueue.fail(lease, exc: BaseException) -> None`
  - `DbTaskQueue.release(lease) -> None`
  - `build_task_queue(settings: QueueSettings | None = None) -> TaskQueue`
  - `app.worker.dispatch.get_job(task_type: str) -> Job`
  - `app.worker.loop.process_one(queue, ai, lease) -> None`
  - `app.worker_main.check_lease(queue_settings, ai_settings) -> None` (ValueError)
  - conftest 픽스처 `sessions` (커밋하는 `sessionmaker`, 끝나면 `task_queue` 비움)

- [ ] **Step 1: `sessions` 픽스처를 conftest 로 옮긴다**

`backend/app/tests/test_task_queue.py` 의 `sessions` 픽스처 정의(docstring 포함)를 잘라 `backend/app/tests/conftest.py` 맨 끝으로 옮긴다. conftest import 에 `from sqlalchemy import text` 와 `from sqlalchemy.orm import sessionmaker` 를 더한다(`Session` import 줄은 `from sqlalchemy.orm import Session, sessionmaker  # noqa: E402` 로). test_task_queue.py 에서는 `sessionmaker` import 를 지운다.

- [ ] **Step 2: 큐 테스트를 새 API 로 다시 쓴다** — `backend/app/tests/test_task_queue.py`

"넣기" 절과 Task 1 에서 넣은 테스트는 그대로 둔다. `# ─── 집기 ───` 줄부터 파일 끝까지를 아래로 바꾼다:

```python
# ─────────────────────────── 집기 ───────────────────────────


@pytest.fixture
def queue(sessions):
    from app.infra.queue import DbTaskQueue, QueueSettings

    return DbTaskQueue(
        sessions,
        settings=QueueSettings(
            QUEUE_MAX_ATTEMPTS=3, QUEUE_BACKOFF_BASE_SEC=30, QUEUE_LEASE_SEC=120
        ),
    )


def _put(sessions, **kwargs) -> Task:
    task = Task(type=kwargs.pop("type", "meal.analyze"), payload=kwargs.pop("payload", {"mealId": "m1"}), **kwargs)
    with sessions() as db:
        db.add(task)
        db.commit()
        db.refresh(task)
    return task


def _expire(sessions, task_id) -> None:
    """lease 를 이미 지난 것으로 만든다. 워커가 죽어 120초가 흐른 상황이다."""
    with sessions() as db:
        db.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
        db.commit()


def test_claim_returns_none_on_empty_queue(queue):
    assert queue.claim() is None


def test_claim_commits_processing_with_a_fresh_lease(sessions, queue):
    """집은 사실이 곧바로 커밋된다 — 다른 세션에서 PROCESSING 이 보여야 한다."""
    put = _put(sessions)
    before = datetime.now(timezone.utc)

    lease = queue.claim()

    assert lease is not None
    assert lease.task.id == put.id
    assert lease.task.type == "meal.analyze"
    assert lease.task.payload == {"mealId": "m1"}
    assert lease.task.attempts == 0  # 이번 시도 이전까지 집힌 횟수
    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PROCESSING
        assert row.attempts == 1
        assert row.lease_token == lease.token
        assert row.lease_expires_at > before + timedelta(seconds=100)


def test_claim_leaves_no_connection_checked_out(sessions, queue, test_engine):
    """claim 이 끝나면 트랜잭션도 커넥션도 남지 않는다 — 이 변경의 목적이다."""
    _put(sessions)

    assert queue.claim() is not None
    assert test_engine.pool.checkedout() == 0


def test_claimed_task_is_not_handed_out_twice(sessions, queue):
    _put(sessions)

    assert queue.claim() is not None
    assert queue.claim() is None


def test_claim_skips_a_row_another_worker_is_claiming(sessions, queue):
    """SKIP LOCKED — 다른 워커가 집는 중(행 잠금)인 행은 기다리지 않고 건너뛴다.

    기다리면 워커를 늘려도 한 줄로 서게 된다.
    """
    first = _put(sessions, payload={"n": 1})
    second = _put(sessions, payload={"n": 2})

    with sessions() as other:
        other.execute(select(Task).where(Task.id == first.id).with_for_update())
        lease = queue.claim()
        other.rollback()

    assert lease is not None
    assert lease.task.id == second.id


def test_task_scheduled_in_the_future_is_not_claimed(sessions, queue):
    _put(sessions, next_run_at=datetime.now(timezone.utc) + timedelta(hours=1))

    assert queue.claim() is None


# ─────────────────────────── 읽기 ───────────────────────────


def test_read_session_never_commits(sessions, queue):
    """`load` 단계 세션은 끝나면 롤백된다 — 실수로 쓴 것도 남지 않는다."""
    _put(sessions)

    with queue.read() as db:
        db.add(Task(type="side.effect", payload={}))
        db.flush()

    with sessions() as db:
        assert db.execute(select(Task.type)).scalars().all() == ["meal.analyze"]


# ─────────────────────────── 완료 ───────────────────────────


def test_complete_commits_done_with_the_result_and_clears_the_lease(sessions, queue):
    put = _put(sessions)
    lease = queue.claim()

    with queue.complete(lease) as done:
        done.result = {"items": 3}

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.DONE
        assert row.result == {"items": 3}
        assert row.finished_at is not None
        assert row.attempts == 1
        assert row.lease_token is None
        assert row.lease_expires_at is None


def test_complete_commits_domain_writes_with_done(sessions, queue):
    """도메인 쓰기와 DONE 은 한 트랜잭션이다."""
    put = _put(sessions)
    lease = queue.claim()

    with queue.complete(lease) as done:
        done.db.add(Task(type="side.effect", payload={}))

    with sessions() as db:
        assert sorted(db.execute(select(Task.type)).scalars().all()) == ["meal.analyze", "side.effect"]
        assert _row(db, put.id).status is TaskStatus.DONE


def test_complete_with_a_lost_lease_rolls_back_domain_writes(sessions, queue):
    """lease 가 만료돼 다른 워커가 가져갔으면 이 워커의 결과는 버린다 — 도메인 쓰기까지."""
    from app.infra.queue import LeaseLostError

    put = _put(sessions)
    lease = queue.claim()
    other_token = uuid.uuid4()
    with sessions() as db:
        db.execute(update(Task).where(Task.id == put.id).values(lease_token=other_token))
        db.commit()

    with pytest.raises(LeaseLostError):
        with queue.complete(lease) as done:
            done.db.add(Task(type="side.effect", payload={}))

    with sessions() as db:
        assert db.execute(select(Task.type)).scalars().all() == ["meal.analyze"]
        row = _row(db, put.id)
        assert row.status is TaskStatus.PROCESSING
        assert row.lease_token == other_token


def test_complete_that_fails_to_commit_raises_and_leaves_the_lease(sessions, queue):
    """DONE 커밋 자체가 깨지면(직렬화 불가 등) 예외가 올라오고 행은 PROCESSING 그대로다.

    루프가 그 예외를 `fail` 로 넘긴다 — 다음 테스트.
    """
    put = _put(sessions)
    lease = queue.claim()

    with pytest.raises(Exception):
        with queue.complete(lease) as done:
            done.result = {"finishedAt": datetime.now(timezone.utc)}

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PROCESSING
        assert row.result is None


# ─────────────────────────── 실패 ───────────────────────────


def test_fail_returns_the_task_to_pending_with_backoff(sessions, queue):
    put = _put(sessions)
    before = datetime.now(timezone.utc)
    lease = queue.claim()

    queue.fail(lease, RuntimeError("AI 가 500 을 냈다"))

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert row.attempts == 1
        assert "RuntimeError" in row.last_error
        assert row.result is None
        assert row.lease_token is None
        assert row.lease_expires_at is None
        # 첫 실패 → 30초 뒤. 곧바로 다시 집으면 재시도가 순식간에 소진된다.
        assert row.next_run_at > before + timedelta(seconds=20)


def test_second_failure_backs_off_longer(sessions, queue):
    put = _put(sessions, attempts=1)
    before = datetime.now(timezone.utc)
    lease = queue.claim()

    queue.fail(lease, RuntimeError("또 실패"))

    with sessions() as db:
        assert _row(db, put.id).next_run_at > before + timedelta(seconds=50)


def test_commit_failure_is_recorded_through_fail(sessions, queue):
    put = _put(sessions)
    lease = queue.claim()

    with pytest.raises(Exception) as caught:
        with queue.complete(lease) as done:
            done.result = {"finishedAt": datetime.now(timezone.utc)}
    queue.fail(lease, caught.value)

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert "TypeError" in row.last_error


def test_task_is_quarantined_after_max_attempts(sessions, queue):
    """3번째로 집힌 시도가 실패하면 FAILED 로 옮기고 더 집지 않는다 — DLQ 자리다."""
    put = _put(sessions, attempts=2)
    lease = queue.claim()

    queue.fail(lease, RuntimeError("세 번째 실패"))

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.FAILED
        assert row.attempts == 3
    assert queue.claim() is None


def test_non_retryable_failure_is_quarantined_on_the_first_attempt(sessions, queue):
    from app.infra.queue import NonRetryableError

    put = _put(sessions)
    lease = queue.claim()

    queue.fail(lease, NonRetryableError("AI 가 422 를 냈다"))

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.FAILED
        assert row.attempts == 1
        assert "NonRetryableError" in row.last_error
    assert queue.claim() is None


def test_late_failure_does_not_revive_a_done_task(sessions, queue):
    """이미 끝난 작업에 뒤늦은 실패 기록이 와도 건드리지 않는다 — 토큰 가드."""
    put = _put(sessions)
    lease = queue.claim()
    with queue.complete(lease) as done:
        done.result = {"ok": True}

    queue.fail(lease, RuntimeError("늦게 도착한 실패 기록"))

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.DONE
        assert row.last_error is None


def test_failure_message_drops_the_sql_dump_and_is_truncated(sessions, queue):
    """IntegrityError 의 str() 은 원본 파라미터(음식명 등)를 붙인다 — 규칙 6."""
    put = _put(sessions)
    lease = queue.claim()

    queue.fail(lease, RuntimeError("x" * 600 + "\n[SQL: INSERT 비밀 음식명]"))

    with sessions() as db:
        row = _row(db, put.id)
        assert "비밀" not in row.last_error
        assert len(row.last_error) <= 500


# ─────────────────────────── 반납 ───────────────────────────


def test_release_does_not_consume_an_attempt(sessions, queue):
    """종료 신호로 놓는 건 실패가 아니다. 배포할 때마다 한 번씩 깎이면 멀쩡한 작업이 격리된다."""
    put = _put(sessions)
    lease = queue.claim()

    queue.release(lease)

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert row.attempts == 0
        assert row.last_error is None
        assert row.lease_token is None
        assert row.next_run_at <= datetime.now(timezone.utc) + timedelta(seconds=1)


# ─────────────────────────── 회수 ───────────────────────────


def test_expired_lease_is_reclaimed_by_the_next_claim(sessions, queue):
    """워커가 죽어 lease 가 지나면 다음 claim 이 PENDING 으로 되돌린다(backoff 적용)."""
    from app.infra.queue import LeaseLostError

    put = _put(sessions)
    lease = queue.claim()
    _expire(sessions, put.id)
    before = datetime.now(timezone.utc)

    assert queue.claim() is None  # 회수된 작업은 backoff 뒤에 다시 집힌다

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PENDING
        assert row.attempts == 1
        assert row.last_error.startswith("LeaseExpired")
        assert row.lease_token is None
        assert row.next_run_at > before + timedelta(seconds=20)

    # 뒤늦게 깨어난 원래 워커의 완료는 반영되지 않는다.
    with pytest.raises(LeaseLostError):
        with queue.complete(lease):
            pass


def test_expired_lease_at_max_attempts_is_quarantined(sessions, queue):
    """워커를 죽이는 작업도 상한에 걸린다 — attempts 를 집을 때 세는 이유다."""
    put = _put(sessions, attempts=2)
    queue.claim()
    _expire(sessions, put.id)

    assert queue.claim() is None

    with sessions() as db:
        assert _row(db, put.id).status is TaskStatus.FAILED


def test_live_lease_is_not_reclaimed(sessions, queue):
    put = _put(sessions)
    lease = queue.claim()

    assert queue.claim() is None

    with sessions() as db:
        row = _row(db, put.id)
        assert row.status is TaskStatus.PROCESSING
        assert row.lease_token == lease.token
```

- [ ] **Step 3: 루프 테스트를 새 API 로 다시 쓴다** — `backend/app/tests/test_worker_loop.py` 전체를 아래로 바꾼다.

```python
"""워커 루프 검증.

이 루프에서 틀리기 쉬운 건 둘이다 — 실패한 작업을 완료로 커밋해 버리는 것(작업이 조용히
사라진다), 그리고 AI 를 기다리는 동안 DB 커넥션을 쥐는 것(이 구조를 만든 이유가 없어진다).
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any

import pytest

from app.infra.queue import ClaimedTask, Completion, Lease, LeaseLostError
from app.worker.dispatch import get_job
from app.worker.job import Job, Skip
from app.worker.loop import process_one, run


class FakeQueue:
    """작업 몇 개를 차례로 빌려 주고 그 뒤로는 None 을 주는 가짜 큐.

    `complete` 블록이 정상으로 끝나면 done 에 담는다. 실패·반납은 호출된 그대로 기록한다.
    """

    def __init__(self, tasks: list[ClaimedTask], *, lose_lease: bool = False) -> None:
        self._tasks = list(tasks)
        self._lose_lease = lose_lease
        self.done: list[tuple[uuid.UUID, dict[str, Any] | None]] = []
        self.failed: list[tuple[uuid.UUID, str]] = []
        self.released: list[uuid.UUID] = []

    def claim(self) -> Lease | None:
        if not self._tasks:
            from app.worker import loop

            loop.request_stop()
            return None
        return Lease(task=self._tasks.pop(0), token=uuid.uuid4())

    @contextmanager
    def read(self):
        yield None

    @contextmanager
    def complete(self, lease: Lease):
        completion = Completion(db=None)
        yield completion
        if self._lose_lease:
            raise LeaseLostError(str(lease.task.id))
        self.done.append((lease.task.id, completion.result))

    def fail(self, lease: Lease, exc: BaseException) -> None:
        self.failed.append((lease.task.id, type(exc).__name__))

    def release(self, lease: Lease) -> None:
        self.released.append(lease.task.id)


class FakeAi:
    pass


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
    return ClaimedTask(id=uuid.uuid4(), type=task_type, payload={"mealId": "m1"}, attempts=attempts)


def _use(monkeypatch, job: Job) -> None:
    monkeypatch.setattr("app.worker.loop.get_job", lambda _task_type: job)


def _boom(*_args):
    raise RuntimeError("AI 가 500 을 냈다")


# ─────────────────────────── 단계와 커밋 시점 ───────────────────────────


def test_success_completes_with_the_apply_result(monkeypatch):
    _use(
        monkeypatch,
        Job(
            load=lambda db, task: {"n": 1},
            call_ai=lambda ctx, ai: {"echo": ctx["n"]},
            apply=lambda db, task, ctx, resp: {"items": resp["echo"]},
        ),
    )
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == [(task.id, {"items": 1})]
    assert queue.failed == []


def test_skip_completes_without_calling_ai(monkeypatch):
    _use(monkeypatch, Job(load=lambda db, task: Skip({"skipped": "DELETED"}), call_ai=_boom, apply=_boom))
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == [(task.id, {"skipped": "DELETED"})]


def test_ai_failure_without_on_ai_error_is_recorded_as_failure(monkeypatch):
    """AI 가 터지면 완료로 커밋하지 않는다 — 실패로 넘겨 재시도에 맡긴다."""
    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=_boom, apply=_boom))
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == []
    assert queue.failed == [(task.id, "RuntimeError")]


def test_ai_failure_with_on_ai_error_completes_with_its_result(monkeypatch):
    _use(
        monkeypatch,
        Job(
            load=lambda db, task: {},
            call_ai=_boom,
            apply=_boom,
            on_ai_error=lambda db, task, ctx, exc: {"status": "FAILED"},
        ),
    )
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == [(task.id, {"status": "FAILED"})]


def test_on_ai_error_that_reraises_is_recorded_as_failure(monkeypatch):
    def reraise(db, task, ctx, exc):
        raise exc

    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=_boom, apply=_boom, on_ai_error=reraise))
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == []
    assert queue.failed == [(task.id, "RuntimeError")]


def test_apply_failure_is_recorded_as_failure(monkeypatch):
    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=lambda ctx, ai: {}, apply=_boom))
    task = _task()
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.done == []
    assert queue.failed == [(task.id, "RuntimeError")]


def test_lost_lease_is_not_recorded_as_failure(monkeypatch):
    """lease 를 잃었으면 그 작업은 이제 다른 워커 몫이다. 실패를 기록하면 남의 시도를 깎는다."""
    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=lambda ctx, ai: {}, apply=lambda *a: None))
    queue = FakeQueue([_task()], lose_lease=True)

    run(queue, FakeAi())

    assert queue.done == []
    assert queue.failed == []


def test_keyboard_interrupt_releases_the_lease(monkeypatch):
    def interrupt(ctx, ai):
        raise KeyboardInterrupt()

    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=interrupt, apply=_boom))
    task = _task()
    queue = FakeQueue([task])

    with pytest.raises(KeyboardInterrupt):
        run(queue, FakeAi())

    assert queue.released == [task.id]
    assert queue.failed == []


def test_one_failure_does_not_stop_the_worker(monkeypatch):
    bad, good = _task(), _task()

    def flaky(ctx, ai):
        if ctx["id"] == bad.id:
            raise RuntimeError("처리 실패")
        return {}

    _use(monkeypatch, Job(load=lambda db, task: {"id": task.id}, call_ai=flaky, apply=lambda *a: None))
    queue = FakeQueue([bad, good])

    run(queue, FakeAi())

    assert queue.failed == [(bad.id, "RuntimeError")]
    assert [task_id for task_id, _result in queue.done] == [good.id]


def test_claim_error_does_not_stop_the_worker(monkeypatch):
    """claim 자체가 터져도(DB 순단) 쉬었다가 다시 돈다."""
    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=lambda ctx, ai: {}, apply=lambda *a: None))
    task = _task()
    queue = FakeQueue([task])
    real_claim = queue.claim
    calls = []

    def flaky_claim():
        calls.append(None)
        if len(calls) == 1:
            raise ConnectionError("DB 가 잠깐 죽었다")
        return real_claim()

    queue.claim = flaky_claim

    run(queue, FakeAi())

    assert [task_id for task_id, _result in queue.done] == [task.id]


def test_unknown_task_type_is_recorded_as_failure():
    task = _task("nope")
    queue = FakeQueue([task])

    run(queue, FakeAi())

    assert queue.failed == [(task.id, "ValueError")]


# ─────────────────────────── 커넥션 ───────────────────────────


def test_no_db_connection_is_held_while_ai_is_called(sessions, test_engine, monkeypatch):
    """진짜 큐로 돌려, AI 를 기다리는 동안 이 워커가 쥔 커넥션이 0 인지 본다.

    이 구조를 만든 이유 그 자체다. 누가 `call_ai` 에 세션을 넘기거나 `read` 블록 안에서
    AI 를 부르게 바꾸면 여기서 걸린다.
    """
    from app.infra.queue import DbTaskQueue
    from app.models.task import Task

    seen: list[int] = []

    def call_ai(ctx, ai):
        seen.append(test_engine.pool.checkedout())
        return {"ok": True}

    _use(monkeypatch, Job(load=lambda db, task: {}, call_ai=call_ai, apply=lambda db, task, ctx, resp: resp))
    with sessions() as db:
        db.add(Task(type="meal.analyze", payload={"mealId": "m1"}))
        db.commit()
    queue = DbTaskQueue(sessions)

    process_one(queue, FakeAi(), queue.claim())

    assert seen == [0]


# ─────────────────────────── 작업 분기 ───────────────────────────


def test_unknown_task_type_raises():
    with pytest.raises(ValueError, match="알 수 없는 작업 타입"):
        get_job("nope")


def test_feedback_types_are_not_collapsed_into_one():
    """피드백 셋을 한 타입으로 묶지 않는다 — 모으는 데이터도 쓰는 테이블도 다르다."""
    with pytest.raises(ValueError, match="알 수 없는 작업 타입"):
        get_job("feedback.generate")


# ─────────────────────────── 기동 검사 ───────────────────────────


def test_lease_must_outlast_the_ai_timeout():
    """lease 가 AI 타임아웃보다 짧으면 정상 작업이 회수돼 두 번 돈다. 기동을 거부한다."""
    from app.infra.ai import AiSettings
    from app.infra.queue import QueueSettings
    from app.worker_main import check_lease

    with pytest.raises(ValueError, match="QUEUE_LEASE_SEC"):
        check_lease(QueueSettings(QUEUE_LEASE_SEC=45), AiSettings(AI_TIMEOUT_SEC=45))

    check_lease(QueueSettings(QUEUE_LEASE_SEC=120), AiSettings(AI_TIMEOUT_SEC=45))
```

- [ ] **Step 4: job 테스트의 `handle(` 호출 네 곳을 바꾼다**

`test_analyze_meal_job.py:459`, `test_feedback_daily_job.py:761`, `test_feedback_long_job.py:709`, `test_feedback_meal_job.py:678` 의 `handle(db, _task(...), FakeAi())` 를 같은 인자 그대로 `get_job(<task>.type).run_inline(db, <task>, FakeAi())` 로 바꾼다. 예 (`test_analyze_meal_job.py`):

```python
    task = _task(meal)
    get_job(task.type).run_inline(db, task, FakeAi())
```

네 파일 import 의 `from app.worker.dispatch import handle` → `from app.worker.dispatch import get_job`.

- [ ] **Step 5: 실패 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_task_queue.py app/tests/test_worker_loop.py -q`
Expected: FAIL — `ImportError: cannot import name 'Completion'` 등

- [ ] **Step 6: `backend/app/infra/queue.py` 를 다시 쓴다** — 파일 전체를 아래로 바꾼다.

```python
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
```

- [ ] **Step 7: `dispatch.py` 를 `Job` 매핑으로** — 모듈 docstring 의 `## 작업을 하나 붙이려면` 절과 `handle` 함수, `_HANDLERS` 를 바꾼다.

docstring 절:

```
## 작업을 하나 붙이려면

1. `jobs/` 에 파일을 만들고 `load` · `call_ai` · `apply` 와 `JOB = Job(...)` 을 둔다
   (계약은 `worker/job.py`)
2. 아래 `_JOBS` 에 한 줄 더한다
3. `_NOT_IMPLEMENTED` 에서 그 타입을 지운다
```

import 줄 `from collections.abc import Callable` · `from typing import Any` · `from sqlalchemy.orm import Session` · `from app.infra.ai import AiClient` · `from app.infra.queue import ClaimedTask` 를 지우고 `from app.worker.job import Job` 를 둔다. `_HANDLERS` 와 `handle` 을 아래로 바꾼다:

```python
_JOBS: dict[str, Job] = {
    "meal.analyze": analyze_meal.JOB,
    "feedback.daily": feedback_daily.JOB,
    "feedback.meal": feedback_meal.JOB,
    "feedback.long": feedback_long.JOB,
}

# 계약은 정해졌지만 아직 구현이 없는 것들. 알 수 없는 타입과 구분해서 알려 준다.
_NOT_IMPLEMENTED: dict[str, str] = {}


def get_job(task_type: str) -> Job:
    """작업 타입에 맞는 `Job` 을 준다.

    쓰기는 `apply` · `on_ai_error` 에서만 한다. 거기서 받는 세션으로 **커밋하는
    `services/` 함수를 부르면 안 된다** — 이 레포 관례상 `services/` 는 커밋을 직접 하는데,
    그러면 DONE 이 커밋되기 전에 도메인 변경만 먼저 굳고, lease 를 잃어도 되돌릴 수 없다.
    커밋하는 service 를 붙여야 하면 커밋 없는 버전으로 쪼개 그 함수를 부른다.
    """
    job = _JOBS.get(task_type)
    if job is not None:
        return job

    if task_type in _NOT_IMPLEMENTED:
        raise NotImplementedError(f"{_NOT_IMPLEMENTED[task_type]} 미구현")

    raise ValueError(f"알 수 없는 작업 타입: {task_type!r}")
```

- [ ] **Step 8: `loop.py` 를 다시 쓴다** — 파일 전체:

```python
"""큐 폴링 루프.

**커밋 시점이 이 파일의 전부다.** 작업 하나를 세 단계로 돌린다:

    load      queue.read()        읽기만 한다. 끝나면 롤백
    call_ai   (세션 없음)          AI 를 기다리는 동안 커넥션을 쥐지 않는다
    apply     queue.complete()    도메인 쓰기 + DONE 을 한 트랜잭션으로 커밋

어디서든 예외가 나면 `queue.fail` 로 넘긴다 — 큐가 PENDING(재시도) 이나 FAILED(격리) 로
돌린다. 종료 신호면 `queue.release` 로 시도를 세지 않고 놓는다. lease 를 잃었으면 아무것도
기록하지 않는다.

작업 핸들러와 파일을 나눈 건 이 때문이다. 여기가 틀리면 작업이 조용히 사라지는데,
핸들러를 붙이다가 실수로 건드리기 쉬운 자리에 두고 싶지 않다.
"""

from __future__ import annotations

import logging
import time

from app.infra.ai import AiClient
from app.infra.queue import Lease, LeaseLostError, QueueSettings, TaskQueue
from app.worker.dispatch import get_job
from app.worker.job import Skip

logger = logging.getLogger("worker")

_running = True


def request_stop() -> None:
    """루프를 멈춘다. 처리 중인 작업은 끝까지 간다."""
    global _running
    _running = False


def process_one(queue: TaskQueue, ai: AiClient, lease: Lease) -> None:
    """빌려 온 작업 하나를 세 단계로 처리한다. 트랜잭션은 단계마다 따로 연다."""
    task = lease.task
    job = get_job(task.type)

    with queue.read() as db:
        ctx = job.load(db, task)

    if isinstance(ctx, Skip):
        with queue.complete(lease) as done:
            done.result = ctx.result
        return

    try:
        response = job.call_ai(ctx, ai)
    except Exception as exc:
        if job.on_ai_error is None:
            raise
        with queue.complete(lease) as done:
            done.result = job.on_ai_error(done.db, task, ctx, exc)
        return

    with queue.complete(lease) as done:
        done.result = job.apply(done.db, task, ctx, response)


def run(queue: TaskQueue, ai: AiClient, settings: QueueSettings | None = None) -> None:
    settings = settings or QueueSettings()
    logger.info("워커 시작. 큐를 폴링한다.")

    while _running:
        try:
            lease = queue.claim()
        except Exception:
            # DB 다운 · task_queue 테이블 부재 · 커넥션 풀 고갈. 안 쉬면 이 실패만 CPU 를
            # 태우며 타이트 루프로 돈다.
            logger.exception("작업을 집지 못했다. 잠시 뒤 다시 시도한다.")
            time.sleep(settings.QUEUE_POLL_INTERVAL_SEC)
            continue

        if lease is None:
            # 롱 폴링이 없으니 직접 쉰다. claim 은 이미 커넥션을 돌려줬다.
            time.sleep(settings.QUEUE_POLL_INTERVAL_SEC)
            continue

        try:
            process_one(queue, ai, lease)
        except LeaseLostError:
            logger.warning(
                "lease 를 잃었다 type=%s — 다른 워커가 회수해 맡았다. 이번 결과는 버린다.",
                lease.task.type,
            )
            continue
        except (KeyboardInterrupt, SystemExit):
            queue.release(lease)
            raise
        except Exception as exc:
            # 포즈 정보가 로그에 남지 않도록 본문은 찍지 않는다(규칙 6).
            logger.exception("작업 처리 실패 type=%s. 재시도에 맡긴다.", lease.task.type)
            queue.fail(lease, exc)
            continue

        # DONE 커밋이 끝난 뒤에 찍어야 로그와 사실이 맞는다.
        logger.info("작업 완료 type=%s", lease.task.type)

    logger.info("워커 종료.")
```

- [ ] **Step 9: `worker_main.py` 기동 검사**

import 를 `from app.infra.ai import AiSettings, build_ai_client` · `from app.infra.queue import QueueSettings, build_task_queue` 로 바꾸고, `_stop` 위에 추가:

```python
def check_lease(queue_settings: QueueSettings, ai_settings: AiSettings) -> None:
    """lease 가 AI 타임아웃보다 길어야 한다. 아니면 정상 작업이 처리 도중 회수돼 두 번 돈다."""
    if queue_settings.QUEUE_LEASE_SEC <= ai_settings.AI_TIMEOUT_SEC:
        raise ValueError(
            f"QUEUE_LEASE_SEC({queue_settings.QUEUE_LEASE_SEC}) 는 "
            f"AI_TIMEOUT_SEC({ai_settings.AI_TIMEOUT_SEC}) 보다 커야 한다"
        )
```

`main()` 의 마지막 줄 `loop.run(build_task_queue(), build_ai_client())` 를 아래로:

```python
    queue_settings = QueueSettings()
    ai_settings = AiSettings()
    check_lease(queue_settings, ai_settings)

    loop.run(build_task_queue(queue_settings), build_ai_client(ai_settings), queue_settings)
```

모듈 docstring 의 "큐는 PostgreSQL 테이블(`task_queue`)이다. 컨테이너를 따로 띄우지 않는다." 다음 줄에 "작업은 lease 로 빌린다 — AI 를 기다리는 동안 DB 커넥션을 쥐지 않는다." 를 더한다.

- [ ] **Step 10: 스모크 스크립트 새 API 로** — `backend/scripts/smoke_queue_ai.py`

`_WrongTaskError` 클래스를 지우고 `_run_once` 를 아래로 바꾼다:

```python
def _run_once(queue: DbTaskQueue, ai) -> None:
    """작업 하나를 빌려 AI 를 부른다. 스텁이 500 을 내면 실패로 기록한다.

    `claim()` 은 마커와 무관하게 가장 오래된 PENDING 행을 빌린다. 로컬 개발 DB 에 이
    스크립트가 넣지 않은 진짜 작업이 있으면 그걸 빌릴 수 있다 — 그러면 곧바로 반납하고
    멈춘다. 반납은 시도 횟수도 되돌리므로 그 작업에 흔적이 남지 않는다.
    """
    lease = queue.claim()
    if lease is None:
        print("    집을 작업이 없다")
        return

    meal_id = lease.task.payload.get("mealId", "")
    if not meal_id.startswith(MARKER_PREFIX):
        queue.release(lease)
        sys.exit(f"    로컬 DB 의 다른 작업을 집었다(mealId={meal_id!r}). 반납했다 — 스모크를 멈춘다.")

    try:
        result = ai.analyze_meal(lease.task.payload)
    except Exception as exc:
        queue.fail(lease, exc)
        print(f"    실패: {type(exc).__name__}")
        return

    with queue.complete(lease) as done:
        done.result = result
    print(f"    시도 {lease.task.attempts + 1}회째 — 성공")
```

`main()` 끝의

```python
    with queue.claim() as claim:
        assert claim is None, "격리된 작업이 다시 집혔다"
```
를 아래로 바꾼다:

```python
    lease = queue.claim()
    if lease is not None:
        queue.release(lease)
    assert lease is None, "격리된 작업이 다시 집혔다"
```

모듈 docstring 의 "작업이 DONE 으로 커밋되지 않고, attempts 가 오르고, 3회째에 FAILED 로 격리되는지." 는 그대로 둔다.

- [ ] **Step 11: 통과 확인**

Run: `.venv/Scripts/python -m pytest app/tests/test_task_queue.py app/tests/test_worker_loop.py app/tests/test_analyze_meal_job.py app/tests/test_feedback_meal_job.py app/tests/test_feedback_daily_job.py app/tests/test_feedback_long_job.py -q`
Expected: 전부 PASS

Run: `.venv/Scripts/python -c "import scripts.smoke_queue_ai, scripts.queue_status, app.worker_main"`
Expected: 에러 없음 (import 만 확인)

- [ ] **Step 12: 커밋**

```bash
git add backend/app/infra/queue.py backend/app/worker/loop.py backend/app/worker/dispatch.py \
  backend/app/worker_main.py backend/scripts/smoke_queue_ai.py backend/app/tests/conftest.py \
  backend/app/tests/test_task_queue.py backend/app/tests/test_worker_loop.py \
  backend/app/tests/test_analyze_meal_job.py backend/app/tests/test_feedback_meal_job.py \
  backend/app/tests/test_feedback_daily_job.py backend/app/tests/test_feedback_long_job.py
git commit -m "[BE-5] refactor: 작업 큐를 lease 방식으로 — AI 호출 동안 트랜잭션·커넥션을 쥐지 않는다" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 운영 스크립트 · 문서

**Files:**
- Modify: `backend/scripts/queue_status.py`
- Modify: `backend/README.md:228-283` (큐 꺼내기 ~ 작업 붙이기 절), `:316` (테스트 표 한 칸)
- Modify: `infra/docker-compose.be.yml:66-70` (worker 주석)
- Modify: `backend/.env.example:21-23`
- Modify: `docs/superpowers/specs/2026-09-20-db-table-queue-design.md:5`

- [ ] **Step 1: `queue_status.py`** — 모듈 docstring 의 두 번째 문단과 `_IN_FLIGHT`, 출력부를 바꾼다.

docstring 두 번째 문단 →

```
처리 중은 `status = 'PROCESSING'` 이다. lease 가 이미 지난 것은 워커가 죽은 것이고,
다음 claim 이 회수한다.
```

`_IN_FLIGHT` →

```python
_IN_FLIGHT = text(
    """
    SELECT type, attempts, now() - updated_at AS elapsed, lease_expires_at < now() AS expired
      FROM task_queue
     WHERE status = 'PROCESSING'
     ORDER BY updated_at
    """
)
```

`main()` 의 처리 중 출력 세 줄(`in_flight = …` 부터 `for pid, elapsed …` 루프까지) →

```python
        in_flight = db.execute(_IN_FLIGHT).all()
        expired = sum(1 for row in in_flight if row.expired)
        print(f"\n처리 중(PROCESSING): {len(in_flight)}  — lease 만료 {expired}건은 다음 claim 이 회수한다")
        for row in in_flight:
            mark = " (만료)" if row.expired else ""
            print(f"  {row.type:<16} attempts={row.attempts} 경과={row.elapsed}{mark}")
```

Run: `.venv/Scripts/python -c "import scripts.queue_status"` → 에러 없음.

- [ ] **Step 2: README 큐 절** — `backend/README.md` 의 `### 꺼내기 — 잠금을 쥔 채로 처리한다` 부터 `### 작업을 하나 붙이려면` 절 끝(`커밋하는 service 가 있으면 커밋 없는 버전으로 쪼개서 쓴다.`)까지를 아래로 바꾼다:

````markdown
### 꺼내기 — lease 로 빌린다

```python
lease = queue.claim()          # PROCESSING · 토큰 · 만료 시각을 쓰고 곧바로 커밋
with queue.read() as db:       # load: 읽기만. 끝나면 롤백
    ...
...                            # call_ai: 세션 없음 — AI 를 기다리는 동안 커넥션 0
with queue.complete(lease) as done:
    done.db                    # apply: 도메인 쓰기
    done.result = {...}        # DONE 과 함께 result 컬럼에 들어간다
queue.fail(lease, exc)         # 실패: PENDING(재시도) 또는 FAILED(격리)
queue.release(lease)           # 종료 신호: 시도를 세지 않고 PENDING 으로
```

`SELECT … FOR UPDATE SKIP LOCKED` 로 한 행을 집되 **집는 순간 커밋한다.** AI 를 기다리는
동안 트랜잭션도 행 잠금도 커넥션도 쥐지 않는다. 완료·실패·반납은 `lease_token` 이 자기
것일 때만 행을 바꾼다. 워커가 죽어 lease(`QUEUE_LEASE_SEC`, 120초)가 지나면 다음 `claim`
이 회수한다.

### 커밋 시점 — 여기가 전부다

| 단계 | 큐가 하는 일 |
|---|---|
| `claim` | `PROCESSING`, `attempts+1`, 토큰, `lease_expires_at` 커밋 |
| `complete` 정상 종료 | 도메인 쓰기 + `DONE` · `result` · `finished_at` 을 한 트랜잭션으로 커밋. 토큰이 안 맞으면 `LeaseLostError` 로 전부 롤백 |
| `fail` | `last_error` · `next_run_at`(30s→60s), 3번째로 집힌 시도면 `FAILED` |
| lease 만료 | 다음 `claim` 이 `PENDING`(backoff) 또는 `FAILED` 로 회수 |

`attempts` 는 **집힌 횟수**다. 워커를 죽이는 작업(OOM·SIGKILL)도 상한에 걸리게 하려고
집을 때 센다. `app/worker/loop.py` 의 `process_one` · `run` 이 이 구조이고,
`app/tests/test_worker_loop.py` 가 "AI 호출 중 커넥션 0" 과 실패 경로를 검증한다.

`FAILED` 는 DLQ 자리다. 자동으로 되살리지 않는다 — 3번 실패한 작업은 대개 코드나
데이터가 잘못된 것이라 사람이 원인을 보고 다시 넣는다.

```sql
UPDATE task_queue SET status='PENDING', attempts=0, next_run_at=now() WHERE id='…';
```

상태는 `python -m scripts.queue_status` 로 본다. 처리 중은 `PROCESSING` 으로 바로 보이고,
lease 가 지난 것도 따로 센다.

### 작업을 하나 붙이려면

1. `app/worker/jobs/` 에 파일을 만들고 `load` · `call_ai` · `apply`(필요하면
   `on_ai_error`)와 `JOB = Job(...)` 을 둔다. 계약은 `app/worker/job.py` 에 있다
2. `app/worker/dispatch.py` 의 `_JOBS` 에 한 줄 더한다
3. `_NOT_IMPLEMENTED` 에서 그 타입을 지운다

`load` 가 넘기는 값은 ORM 객체가 아닌 순수 데이터여야 한다 — `load` 의 세션은 곧 닫힌다.
`call_ai` 는 세션을 받지 않는다. 쓰기는 `apply` · `on_ai_error` 에서만 한다.

**`apply` 에서 부르는 `services/`·`crud/` 함수는 커밋하면 안 된다.** 커밋은 큐가 DONE 과
함께 한다 — 도중에 커밋하는 service 를 부르면 lease 를 잃었을 때 그 변경을 되돌릴 수 없다.
커밋하는 service 가 있으면 커밋 없는 버전으로 쪼개서 쓴다.
````

316행 테스트 표의 작업 큐 칸 → `실제 Postgres 로 lease·SKIP LOCKED·재시도·격리·회수 검증(`test_task_queue.py`). AI 호출 중 커넥션 0 을 `test_worker_loop.py` 가 확인한다`

- [ ] **Step 3: compose 주석** — `infra/docker-compose.be.yml` worker 의 `stop_grace_period` 위 주석 네 줄을 아래로 바꾼다 (값 `60s` 는 그대로):

```yaml
    # SIGTERM 을 받으면 워커는 진행 중인 작업을 끝까지 처리하고 내려간다. compose 기본
    # 유예는 10초뿐이라 AI 타임아웃(AI_TIMEOUT_SEC=45)보다 짧고, 그러면 배포할 때마다
    # SIGKILL 이 떨어진다. 강제 종료된 작업은 PROCESSING 으로 남았다가 lease
    # (QUEUE_LEASE_SEC=120)가 지나서야 회수되고, 그때 시도를 하나 태운다.
```

- [ ] **Step 4: `.env.example`** — `QUEUE_BACKOFF_BASE_SEC=30` 아래에 `QUEUE_LEASE_SEC=120` 한 줄 추가.

- [ ] **Step 5: 옛 spec 표시** — `docs/superpowers/specs/2026-09-20-db-table-queue-design.md` 5행 `상태: …` 아래에 한 줄 추가:

```
대체됨: "처리하는 동안 행 잠금을 유지한다" 결정은 `2026-10-05-queue-lease-design.md`(lease 방식)로 대체됐다.
```

- [ ] **Step 6: 커밋**

```bash
git add backend/scripts/queue_status.py backend/README.md infra/docker-compose.be.yml \
  backend/.env.example docs/superpowers/specs/2026-09-20-db-table-queue-design.md
git commit -m "[BE-5] docs: 큐 lease 전환에 맞춰 README·운영 스크립트·compose 주석 갱신" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 전체 검증

- [ ] **Step 1: 전체 테스트**

Run: `.venv/Scripts/python -m pytest app/tests -q`
Expected: 전부 PASS. 실패가 있으면 출력 그대로 보고하고 원인을 고친다.

- [ ] **Step 2: 남은 옛 API 흔적 검색**

Run: `grep -rnE "claim\(\) as|QUEUE_IDLE_TX_TIMEOUT_SEC|_record_failure|dispatch import handle|\.run_inline\b" backend/app backend/scripts --include=*.py`
Expected: `run_inline` 은 `app/worker/job.py` 정의와 `app/tests/` 안에서만 나온다. 나머지 패턴은 0건.

- [ ] **Step 3: lint (레포에 ruff 가 있으면)**

Run: `.venv/Scripts/python -m ruff check app scripts`
Expected: 새 경고 없음 (ruff 가 설치돼 있지 않으면 이 단계를 건너뛰고 그렇다고 보고한다)

- [ ] **Step 4: (선택, 로컬 DB·ai-stub 이 떠 있을 때) 스모크**

Run: `.venv/Scripts/python -m scripts.smoke_queue_ai`
Expected: `왕복 확인 완료.` — 띄울 수 없으면 건너뛰고 보고한다.
