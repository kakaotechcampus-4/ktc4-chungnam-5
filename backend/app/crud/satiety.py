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
from app.models.satiety import SatietyCheckin


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


def set_hunger_return(
    db: Session, *, meal_id: uuid.UUID, minutes: int | None, comment: str | None
) -> SatietyLog:
    """체크인이 함께 보낸 "다시 배고파진 시각" 과 한마디를 기록한다.

    체크인 행이 아니라 **`satiety_logs`** 에 넣는다. 명세의 `GET /meals/{mealId}` 가
    이 둘을 `checkins[]` **바깥**에 두기 때문이다 — 식사당 하나다.

        "satiety": { "beforePct": …, "afterPct": …,
                     "checkins": [ … ],
                     "hungerReturnMinutes": 60 }

    **`satiety_before` · `satiety_after` 는 건드리지 않는다.** 식사 등록과 확정이
    쓰는 칸이라, 체크인이 덮으면 그 기록이 사라진다 (`set_satiety_after` 와 같은 이유).

    **`None` 은 덮지 않는다.** 체크인마다 두 값을 다 보내지는 않는데, 안 보낸 것을
    NULL 로 쓰면 앞서 적어 둔 값이 지워진다. "안 보냈다" 와 "지워 달라" 는 다르다.
    """
    now = datetime.now(UTC)
    values = {"meal_id": meal_id, "logged_at": now}
    updates: dict[str, object] = {}
    if minutes is not None:
        values["hunger_return_minutes"] = minutes
        updates["hunger_return_minutes"] = minutes
    if comment is not None:
        values["user_comment"] = comment
        updates["user_comment"] = comment

    stmt = insert(SatietyLog).values(**values)
    if updates:
        stmt = stmt.on_conflict_do_update(
            index_elements=[SatietyLog.meal_id], set_=updates
        )
    else:
        # 둘 다 안 보냈다. 행이 없으면 만들기만 하고, 있으면 그대로 둔다.
        stmt = stmt.on_conflict_do_nothing(index_elements=[SatietyLog.meal_id])
    db.execute(stmt)
    db.flush()

    row = get_by_meal(db, meal_id)
    assert row is not None  # 방금 만들었거나 이미 있었다
    return row


def upsert_checkin(
    db: Session, *, meal_id: uuid.UUID, offset_hours: int, pct: int
) -> SatietyCheckin:
    """사후 포만감 한 건. add/flush 까지만 — 커밋은 services 가 한다.

    **같은 시점을 다시 보내면 덮는다** (`UNIQUE (meal_id, checkin_offset_hours)`).
    "식후 3시간 포만감" 은 하나이고, 더블탭이나 오입력이 그래프에 점 두 개를 만들면
    안 된다.

    **`ON CONFLICT` 한 문장이다.** 읽고 나서 넣으면 두 요청이 겹칠 때 두 번째가
    UNIQUE 위반으로 죽는다 (`crud/evaluation.py::upsert` 와 같은 이유).

    덮어써도 `id` 는 유지된다 — `ON CONFLICT DO UPDATE` 는 기존 행을 고치므로,
    같은 시점의 체크인은 늘 같은 `checkinId` 를 갖는다.
    """
    stmt = (
        insert(SatietyCheckin)
        .values(meal_id=meal_id, checkin_offset_hours=offset_hours, satiety_pct=pct)
        .on_conflict_do_update(
            index_elements=[
                SatietyCheckin.meal_id,
                SatietyCheckin.checkin_offset_hours,
            ],
            set_={"satiety_pct": pct},
        )
        .returning(SatietyCheckin)
    )
    row = db.execute(stmt).scalar_one()
    db.flush()
    return row
