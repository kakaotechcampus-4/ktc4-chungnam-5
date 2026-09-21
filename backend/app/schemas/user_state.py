"""컨디션 기록(user_states) API 요청/응답 스키마.

요청은 Decimal 로 받고 응답은 float 로 내보낸다 — schemas/user.py 와 같은 이유
(pydantic v2 는 Decimal 을 JSON 문자열로 직렬화한다).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from app.schemas.base import CamelModel


class GiSymptom(CamelModel):
    """위장관 증상 하나. 증상마다 강도를 따로 받는다."""

    code: str
    severity: str


class UserStateCreateRequest(CamelModel):
    """POST /user-states 요청."""

    weight_kg: Decimal
    appetite_level: int | None = None
    gi_symptoms: list[GiSymptom] = []
    note: str | None = None
    recorded_at: datetime | None = None
    """없으면 서버 현재 시각."""


class UserStateResponse(CamelModel):
    """POST /user-states 201 응답. GET /user-states/latest 가 재사용한다."""

    user_state_id: uuid.UUID
    weight_kg: float | None
    weight_change_kg: float | None
    """이번 체중 − 지난주 비교 대상 체중. 비교 대상이 없으면 None (0 이 아니다)."""
    weight_change_baseline: str | None
    appetite_level: int | None
    gi_symptoms: list[GiSymptom]
    note: str | None
    recorded_at: datetime
