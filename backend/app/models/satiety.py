"""사후 포만감 체크인.

`satiety_logs` 는 식사당 1행이라 "식전 · 식후" 두 값만 담는다. 명세의
`GET /meals/{mealId}` 는 그 바깥에 `checkins[]` 를 따로 두고, 사용자는 식후 몇 시간
뒤 포만감을 **여러 번** 보낼 수 있다. 그래서 행이 따로 필요하다.

**JSONB 컬럼이 아니라 테이블인 이유**

- 명세 응답이 `checkinId` 를 준다. 행이 없으면 식별자를 지어내야 하고, 배열 인덱스를
  쓰면 순서가 바뀔 때 깨진다.
- 동시 체크인. JSONB 배열에 더하려면 읽고-고치고-쓰기라 두 요청이 겹치면 하나가
  사라진다. 테이블이면 `UNIQUE` + `ON CONFLICT` 한 문장으로 끝난다.
- 이 코드베이스가 반대 방향으로 간 선례가 있다 — 양을 `raw_ai_result` JSON 에서
  컬럼으로 꺼냈고(`README` 의 양 6컬럼 규약) 그 이유를 남겨 두었다.

**`models/meal.py` 가 아니라 여기 두는 이유**: 그 파일은 `meals` · `meal_items` 를
함께 담은 다른 담당 영역이다. `SatietyLog` 가 거기 있어 한 도메인이 두 파일로
갈리지만, 남의 파일을 건드리지 않는 쪽을 택했다 — `SatietyLog` 를 이리로 옮기는 건
그 파일 담당자와 이야기할 것.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import created_at, uuid_pk


class SatietyCheckin(Base):
    """식후 몇 시간 뒤의 포만감 한 건. 명세 `POST /meals/{mealId}/satiety-checkins`."""

    __tablename__ = "satiety_checkins"

    id: Mapped[uuid.UUID] = uuid_pk()
    """응답의 `checkinId` 다."""

    meal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meals.id", ondelete="CASCADE"), nullable=False
    )
    """**따로 인덱스를 두지 않는다.** 아래 `UNIQUE (meal_id, checkin_offset_hours)` 의
    인덱스가 `meal_id` 를 선두 컬럼으로 가져서, `meal_id` 만으로 찾는 조회도 그걸 탄다.
    하나 더 만들면 쓰기마다 갱신할 인덱스만 늘어난다."""

    checkin_offset_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    """식후 몇 시간 뒤인지. 절대 시각이 아니라 **식사로부터의 간격**이다 —
    명세가 그 모양이고, 끼니끼리 비교하려면 시각보다 간격이 맞다."""

    satiety_pct: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = created_at()

    __table_args__ = (
        UniqueConstraint(
            "meal_id", "checkin_offset_hours", name="uq_satiety_checkins_meal_offset"
        ),
        CheckConstraint(
            "checkin_offset_hours BETWEEN 0 AND 48",
            name="satiety_checkins_offset_range",
        ),
        CheckConstraint(
            "satiety_pct BETWEEN 0 AND 100", name="satiety_checkins_pct_range"
        ),
    )
    """`(meal_id, checkin_offset_hours)` UNIQUE 는 **같은 시점 재전송을 덮어쓰기로**
    만든다. "식후 3시간 포만감" 은 하나이고, 더블탭이나 오입력이 그래프에 점 두 개를
    만들면 안 된다 — `qqs_evaluations` · `satiety_logs` 와 같은 판단이다.

    상한 48시간은 명세에 근거가 없는 방어값이다. 한 끼의 포만감을 이틀 뒤에 보고하는
    건 입력 실수에 가깝고, 없으면 `checkinOffsetHours: 100000` 이 그대로 들어간다."""
