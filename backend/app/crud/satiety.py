"""satiety_logs 접근.

`(meal_id)` UNIQUE 라 create 가 아니라 **upsert** 다 (`crud/__init__.py` 목록).
`POST /meals` 의 `satietyBeforePct` 와 `POST /confirm` 의 `satietyAfterPct` 가
같은 행의 다른 칸을 채운다 — 먼저 오는 쪽이 행을 만든다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.meal import SatietyLog


def get_by_meal(db: Session, meal_id: uuid.UUID) -> SatietyLog | None:
    stmt = select(SatietyLog).where(SatietyLog.meal_id == meal_id)
    return db.execute(stmt).scalar_one_or_none()


def set_satiety_after(db: Session, *, meal_id: uuid.UUID, pct: int) -> SatietyLog:
    """식후 포만감을 기록한다. add/flush 까지만 — 커밋은 services 가 한다.

    **`satiety_before` 는 건드리지 않는다.** 식사 등록 때 받은 값이라 확정 단계에서
    덮으면 "먹기 전" 기록이 사라진다.

    `logged_at` 은 서버 기본값이 없는 NOT NULL 이라 **처음 만들 때만** 채운다.
    충돌 시에는 건드리지 않는다 — 한 행에 식전·식후 두 값이 들어 있어 타임스탬프
    하나가 둘 다를 뜻할 수 없다. 덮으면 식전 기록 시각이 사라진다.
    식후 시각이 따로 필요해지면 컬럼을 나눈다 (satiety-checkins 티켓).

    **`ON CONFLICT` 한 문장이다.** 읽고 나서 넣으면 `(meal_id)` UNIQUE 경합에서
    두 번째 요청이 죽는다 (`crud/evaluation.py::upsert` 와 같은 이유).
    `set_` 에 `satiety_before` 를 넣지 않는 게 핵심이다 — 충돌 시 그 칸은 그대로 둔다.
    """
    now = datetime.now(UTC)
    stmt = insert(SatietyLog).values(
        meal_id=meal_id, satiety_after=pct, logged_at=now
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[SatietyLog.meal_id],
        set_={"satiety_after": pct},
    ).returning(SatietyLog)
    row = db.execute(stmt).scalar_one()
    db.flush()
    return row
