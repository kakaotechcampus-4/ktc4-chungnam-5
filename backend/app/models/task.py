"""작업 큐 행.

큐를 별도 미들웨어로 두지 않고 테이블 하나로 처리한다. 워커는 SELECT … FOR UPDATE
SKIP LOCKED 로 한 행을 집어 **lease 로 빌린다** — PROCESSING ·
토큰 · 만료 시각을 쓰고 곧바로 커밋한다. AI 를 기다리는 동안 트랜잭션을 쥐지 않는다.

설계 배경은 docs/superpowers/specs/2026-10-05-queue-lease-design.md 에 있다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, Text, Uuid, func, text
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
    """지금까지 **집힌** 횟수. claim 이 1 올린다. 첫 시도 전에는 0 이다."""
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
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    """claim 할 때마다 새로 발급한다. 완료·실패·반납은 이 값이 자기 것일 때만 행을 바꾼다."""
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """이 시각이 지나도 PROCESSING 이면 다른 워커가 회수한다. PROCESSING 이 아니면 NULL 이다."""

    __table_args__ = (
        # 집는 쿼리 전용 부분 인덱스. DONE 행이 쌓여도 이 인덱스는 커지지 않는다.
        # 컬럼 순서는 claim 쿼리의 `ORDER BY next_run_at, created_at` 과 반드시 같아야
        # 한다 — 어긋나면 인덱스가 정렬을 못 태워 LIMIT 1 이전에 PENDING 을 전부 읽는다.
        Index(
            "ix_task_queue_pending",
            "next_run_at",
            "created_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
        # 만료된 lease 회수(`DbTaskQueue._reclaim_expired`) 전용. PROCESSING 은 워커 수만큼만
        # 있으므로 이 인덱스는 늘 작다.
        Index(
            "ix_task_queue_processing",
            "lease_expires_at",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
    )
