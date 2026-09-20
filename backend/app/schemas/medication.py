"""투약 도메인 요청·응답 스키마.

응답 필드는 camelCase 다 — FE 와 맞춘 API 명세를 따른다. 내부 파이썬 코드는
snake_case 를 그대로 쓰고, 직렬화 시점에만 alias 로 바꾼다.

**요청은 Decimal, 응답은 float 다.** pydantic v2 는 JSON 직렬화에서 Decimal 을
문자열로 쓴다 — 그대로 두면 `"doseMg": 0.25` 가 `"0.250"` 으로 나간다.
입력은 Decimal 로 받아 정밀도를 지키고(Numeric(6,3) 컬럼), 출력에서만 float 로 바꾼다.
"""

from datetime import date
from decimal import Decimal

from pydantic import Field

from app.models.enums import DrugName, MedicationStage
from app.schemas.base import CamelModel


class MedicationUpsertRequest(CamelModel):
    """투약 정보 등록·수정 겸용.

    `startedAt` 은 **이번에 맞은 날이 아니라 전체 투약 시작일**이다. 명세의 서버 계산
    항목이 여기서 나온다 — `doseCount = floor((today - startedAt) / 7) + 1`.
    FE 의 회차 스테퍼도 이 값을 역산해서 보낸다.

    단계(stage)는 받지 않는다. 서버가 용량으로 판정한다 — 사용자가 고르게 하면
    아키텍처의 Stage Rule Engine 이 사라진다.
    """

    drug_name: DrugName
    dose_mg: Decimal = Field(gt=0, le=Decimal("999.999"))
    started_at: date | None = Field(
        default=None,
        description="**전체 투약 시작일.** 회차를 여기서 역산한다. 생략하면 오늘.",
    )


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
