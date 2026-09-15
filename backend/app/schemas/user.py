"""사용자 프로필 API 요청/응답 스키마.

요청은 Decimal 로 받고 응답은 float 로 내보낸다. pydantic v2 는 JSON 직렬화에서
Decimal 을 문자열로 쓰기 때문에, Decimal 그대로 두면 명세의 `"heightCm": 174.0` 이
`"174.0"` 으로 나간다.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.schemas.base import CamelModel


class OnboardingStatus(str, enum.Enum):
    """FE 의 진입 화면을 정한다. DB 컬럼이 아니라 파생값이다."""

    PROFILE_REQUIRED = "PROFILE_REQUIRED"
    MEDICATION_REQUIRED = "MEDICATION_REQUIRED"
    READY = "READY"


class ProfileCreateRequest(CamelModel):
    """POST /users/profile 요청."""

    nickname: str = Field(min_length=1, max_length=64)
    height_cm: Decimal = Field(gt=0, le=300)
    weight_kg: Decimal = Field(gt=0, le=500)
    baseline_intake: Decimal = Field(gt=0, le=10000)
    """투약 전 평소 한 끼 열량(kcal). Quantity 감소폭의 분모 (D7)."""


class ProfileUpdateRequest(CamelModel):
    """PATCH /users/me 요청. 준 필드만 바꾼다."""

    nickname: str | None = Field(default=None, min_length=1, max_length=64)
    height_cm: Decimal | None = Field(default=None, gt=0, le=300)
    weight_kg: Decimal | None = Field(default=None, gt=0, le=500)
    baseline_intake: Decimal | None = Field(default=None, gt=0, le=10000)


class UserProfileResponse(CamelModel):
    """GET /users/me · PATCH /users/me 응답."""

    user_id: uuid.UUID
    nickname: str
    height_cm: float | None
    weight_kg: float | None
    baseline_intake: float
    onboarding_status: OnboardingStatus


class ProfileCreatedResponse(UserProfileResponse):
    """POST /users/profile 201 응답. 생성 시각이 하나 더 붙는다."""

    created_at: datetime
