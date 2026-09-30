"""홈(GET /home) 조립 로직.

DB 는 crud/ 를 통해서만 만진다 (규칙 5). 다른 services/ 모듈을 import 하지 않는다 —
투약 계산(services/medication.get_current_view)은 엔드포인트가 호출해 결과만 넘긴다.
읽기 전용이라 커밋하지 않는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.schemas.home import HomeResponse
from app.schemas.medication import CurrentMedicationResponse


def get_home(
    db: Session,
    *,
    user_id: uuid.UUID,
    medication: CurrentMedicationResponse | None,
    now: datetime,
) -> HomeResponse:
    """홈 화면 한 번에 필요한 데이터를 조립한다.

    medication 은 엔드포인트가 구한 GET /medications/current 와 같은 계산 결과다 (미등록이면 None).
    now 는 KST aware 시각 — "오늘"과 minutesSinceMeal 이 같은 순간을 쓰게 한다.
    없는 사용자는 404 USER_NOT_FOUND.
    """
    raise NotImplementedError("get_home 미구현")
