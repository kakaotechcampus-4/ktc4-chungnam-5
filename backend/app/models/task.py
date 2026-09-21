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
        # 컬럼 순서는 claim 쿼리의 `ORDER BY next_run_at, created_at` 과 반드시 같아야
        # 한다 — 어긋나면 인덱스가 정렬을 못 태워 LIMIT 1 이전에 PENDING 을 전부 읽는다.
        Index(
            "ix_task_queue_pending",
            "next_run_at",
            "created_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )
