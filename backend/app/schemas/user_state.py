"""컨디션 기록(user_states) API 요청/응답 스키마.

요청은 Decimal 로 받고 응답은 float 로 내보낸다 — schemas/user.py 와 같은 이유
(pydantic v2 는 Decimal 을 JSON 문자열로 직렬화한다).

증상 code · severity 는 여기서만 검증한다. DB(gi_symptoms JSONB)에는 enum 이 없다.
검증 오류 메시지에 입력값(체중·증상·메모)을 넣지 않는다 (README 규칙 6).
"""

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from pydantic import AwareDatetime, ConfigDict, Field, field_validator

from app.schemas.base import CamelModel, KstDatetime

WEIGHT_CHANGE_BASELINE_LAST_WEEK = "LAST_WEEK"


class GiSymptomCode(str, enum.Enum):
    """위장관 증상 종류."""

    NAUSEA = "NAUSEA"
    VOMITING = "VOMITING"
    HEARTBURN = "HEARTBURN"
    CONSTIPATION = "CONSTIPATION"
    DIARRHEA = "DIARRHEA"
    BLOATING = "BLOATING"
    ABDOMINAL_PAIN = "ABDOMINAL_PAIN"


class GiSeverity(str, enum.Enum):
    """증상 강도. 화면의 약함 / 보통 / 심함."""

    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"


class GiSymptom(CamelModel):
    """위장관 증상 하나. 증상마다 강도를 따로 받는다.

    extra="forbid": 증상 항목 안의 오타 필드도 조용히 버리지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    code: GiSymptomCode
    severity: GiSeverity


class UserStateCreateRequest(CamelModel):
    """POST /user-states 요청.

    extra="forbid": 오타난 필드(예: weigthKg)를 조용히 무시하지 않는다 —
    schemas/user.py ProfileCreateRequest 와 같은 이유.
    """

    model_config = ConfigDict(extra="forbid")

    weight_kg: Decimal = Field(gt=0, le=500)
    appetite_level: int | None = Field(default=None, ge=1, le=5)
    """없음=1, 약함=2, 보통=3, 강함=4, 매우 강함=5."""
    gi_symptoms: list[GiSymptom] = []
    """"증상 없음" 은 [] 하나로만 표현한다 (null 은 422)."""
    note: str | None = None
    recorded_at: AwareDatetime | None = None
    """없으면 서버 현재 시각. 시간대 없는 값은 KST/UTC 를 추측하지 않고 거부한다."""

    @field_validator("gi_symptoms")
    @classmethod
    def _reject_duplicate_codes(cls, value: list[GiSymptom]) -> list[GiSymptom]:
        # 같은 code 가 두 번 오면 어느 강도가 맞는지 서버가 고를 수 없다.
        codes = [symptom.code for symptom in value]
        if len(codes) != len(set(codes)):
            raise ValueError("같은 증상이 두 번 들어왔습니다.")
        return value

    @field_validator("recorded_at")
    @classmethod
    def _reject_future(cls, value: datetime | None) -> datetime | None:
        # 허용 오차 0 (D6). 지금 시각이 필요하면 FE 는 필드를 생략한다.
        if value is not None and value > datetime.now(timezone.utc):
            raise ValueError("기록 시각이 미래입니다.")
        return value


class UserStateResponse(CamelModel):
    """POST /user-states 201 응답. GET /user-states/latest 가 재사용한다."""

    user_state_id: uuid.UUID
    weight_kg: float | None
    weight_change_kg: float | None
    """이번 체중 − 지난주 비교 대상 체중. 비교 대상이 없으면 None (0 이 아니다)."""
    weight_change_baseline: str | None
    """비교 기준. 지금은 항상 "LAST_WEEK" — 비교 대상이 없어도 같다 (D4)."""
    appetite_level: int | None
    gi_symptoms: list[GiSymptom]
    note: str | None
    recorded_at: KstDatetime
