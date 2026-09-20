"""투약 도메인 요청·응답 스키마.

응답 필드는 camelCase 다 — FE 와 맞춘 API 명세를 따른다. 내부 파이썬 코드는
snake_case 를 그대로 쓰고, 직렬화 시점에만 alias 로 바꾼다.

**요청은 Decimal, 응답은 float 다.** pydantic v2 는 JSON 직렬화에서 Decimal 을
문자열로 쓴다 — 그대로 두면 `"doseMg": 0.25` 가 `"0.250"` 으로 나간다.
입력은 Decimal 로 받아 정밀도를 지키고(Numeric(6,3) 컬럼), 출력에서만 float 로 바꾼다.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from app.models.enums import DrugName, MedicationStage
from app.schemas.base import CamelModel


class MedicationUpsertRequest(CamelModel):
    """투약 정보 등록·수정 겸용.

    `startedAt` 은 **이번에 맞은 날이 아니라 전체 투약 시작일**이다. 명세의 서버 계산
    항목이 여기서 나온다 — `doseCount = floor((today - startedAt) / 7) + 1`.
    FE 의 회차 스테퍼도 이 값을 역산해서 보낸다.

    단계(stage)는 받지 않는다. 서버가 용량으로 판정한다 — 사용자가 고르게 하면
    아키텍처의 Stage Rule Engine 이 사라진다.

    extra="forbid": 오타난 필드(예: `startedAtt`)를 조용히 무시하지 않는다. 무시하면
    `startedAt` 이 빠진 것으로 처리돼 **오늘로 등록되고 회차가 1 로 리셋된다** —
    클라이언트는 200 을 받고 저장됐다고 믿는다. `schemas/user.py` 의 요청 스키마들과
    같은 이유다.
    """

    model_config = ConfigDict(extra="forbid")

    drug_name: DrugName
    dose_mg: Decimal = Field(gt=0, le=Decimal("999.999"))
    started_at: date | None = Field(
        default=None,
        description="**전체 투약 시작일.** 회차를 여기서 역산한다. 생략하면 오늘.",
    )


class DoseDirection(str, enum.Enum):
    """이전 용량 대비 방향. 명세 `doseEvent.direction` 이다.

    DB 에 저장하지 않는다 — 이전 행의 용량과 비교하면 언제든 다시 나오는 값이라
    컬럼으로 두면 두 진실이 생긴다.
    """

    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    MAINTAIN = "MAINTAIN"
    """첫 등록. 비교할 이전 용량이 없다.

    명세 `GET /medications/dose-events` 예시의 `de_001` 이 이 경우다.
    같은 용량으로 다시 보낸 경우는 여기 해당하지 않는다 — 그건 이벤트 자체가 안 생긴다.
    """


class DoseEvent(CamelModel):
    """용량 변경 1건. `medication_records` 행 하나가 그대로 한 이벤트다.

    명세에 `dose_events` 라는 별도 테이블이 있는 것처럼 적혀 있지만, 우리 모델은
    **행 하나가 곧 용량 변경 1건**이라 `medication_records` 가 그 테이블이다
    (「행 하나 = 용량 변경 1건」 참고). `doseEventId` 는 그 행의 id 다.
    """

    dose_event_id: uuid.UUID
    dose_mg: float
    direction: DoseDirection
    effective_from: date = Field(description="이 용량으로 바꾼 날")


class MedicationUpsertResponse(CamelModel):
    """`POST /medications` 응답.

    **`GET /medications/current` 와 필드가 다르다.** 겹치는 건 현재 상태 5개뿐이고,
    이쪽은 "이번 요청으로 무엇이 바뀌었는가"(`doseChanged` · `doseEvent` ·
    `stageChanged` · `decidedAt`)를 함께 내린다. 그래서 스키마를 따로 둔다 —
    `CurrentMedicationResponse` 를 재사용하면 변경 여부를 실을 자리가 없다.

    명세에 없는 `effectiveFrom` 은 여기서 내리지 않는다. 현재 용량으로 바꾼 날은
    `doseEvent.effectiveFrom` 에 이미 들어 있다.
    """

    medication_id: uuid.UUID = Field(
        description="방금 반영된 `medication_records` 행 id."
    )
    drug_name: DrugName
    dose_mg: float
    started_at: date = Field(description="전체 투약 시작일")
    dose_count: int = Field(description="floor((today - startedAt) / 7) + 1")
    next_dose_date: date = Field(description="startedAt + 7 x doseCount")
    days_until_next_dose: int = Field(description="nextDoseDate - today. 구조상 1~7")
    stage: MedicationStage
    stage_reason: str = Field(description="단계를 사용자에게 설명하는 한 줄")
    rule_version: str = Field(description="판정에 쓰인 규칙 버전")
    dose_changed: bool = Field(description="이번 요청으로 약·용량이 바뀌었는지")
    dose_event: DoseEvent | None = Field(
        default=None, description="바뀌었다면 그 변경 1건. 아니면 null"
    )
    stage_changed: bool = Field(description="이번 요청으로 단계 판정이 달라졌는지")
    decided_at: datetime = Field(description="판정 시각")


class CurrentMedicationResponse(CamelModel):
    """`GET /medications/current`.

    투약 기록이 없는 사용자도 200 이다 — stage=PRE_DOSE 에 나머지가 전부 null.
    404 로 내리면 FE 가 "아직 투약 전"과 "에러"를 구분하지 못한다.
    """

    stage: MedicationStage
    drug_name: DrugName | None = None
    dose_mg: float | None = None
    dose_count: int | None = Field(
        default=None, description="투약 회차 = floor((today - startedAt) / 7) + 1"
    )
    started_at: date | None = Field(default=None, description="전체 투약 시작일")
    effective_from: date | None = Field(
        default=None, description="현재 용량으로 바꾼 날"
    )
    next_dose_date: date | None = Field(
        default=None,
        description="다음 투약 예정일 = startedAt + 7 x doseCount. **예정이지 사실이 아니다.**",
    )
    days_until_next_dose: int | None = Field(
        default=None, description="D-day = nextDoseDate - today. 구조상 1~7 이다."
    )
