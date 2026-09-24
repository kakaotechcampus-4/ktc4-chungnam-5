"""투약 도메인 요청·응답 스키마.

응답 필드는 camelCase 다 — FE 와 맞춘 API 명세를 따른다. 내부 파이썬 코드는
snake_case 를 그대로 쓰고, 직렬화 시점에만 alias 로 바꾼다.

**요청은 Decimal, 응답은 float 다.** pydantic v2 는 JSON 직렬화에서 Decimal 을
문자열로 쓴다 — 그대로 두면 `"doseMg": 0.25` 가 `"0.250"` 으로 나간다.
입력은 Decimal 로 받아 정밀도를 지키고(Numeric(6,3) 컬럼), 출력에서만 float 로 바꾼다.
"""

import enum
import uuid
from datetime import date
from decimal import Decimal

from pydantic import ConfigDict, Field

from app.models.enums import DrugName, MedicationStage
from app.schemas.base import CamelModel, KstDatetime


class MedicationRegisterRequest(CamelModel):
    """투약 정보 등록. 정정은 `PATCH /medications/{id}` 다.

    `startedAt` 은 **이번에 맞은 날이 아니라 전체 투약 시작일**이다. 명세의 서버 계산
    항목이 여기서 나온다 — `doseCount = floor((today - startedAt) / 7) + 1`.
    FE 의 회차 스테퍼도 이 값을 역산해서 보낸다.

    단계(stage)는 받지 않는다. 서버가 용량으로 판정한다 — 사용자가 고르게 하면
    아키텍처의 Stage Rule Engine 이 사라진다.

    extra="forbid": 오타난 필드(예: `startedAtt`)를 조용히 무시하지 않는다. 무시하면
    `startedAt` 이 빠진 것으로 처리돼 **첫 등록이 오늘 날짜로 들어가고 회차가 1 이 된다** —
    클라이언트는 200 을 받고 저장됐다고 믿는다. `schemas/user.py` 의 요청 스키마들과
    같은 이유다.
    """

    model_config = ConfigDict(extra="forbid")

    drug_name: DrugName
    dose_mg: Decimal = Field(gt=0, le=Decimal("999.999"))
    started_at: date | None = Field(
        default=None,
        description=(
            "**전체 투약 시작일.** 회차를 여기서 역산한다. "
            "생략하면 기존 시작일을 그대로 둔다 — 첫 등록일 때만 오늘."
        ),
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


class DoseEventsResponse(CamelModel):
    """`GET /medications/dose-events`.

    명세가 `{ "events": [...] }` 로 감싼다 — 배열을 그대로 내보내지 않는다. 나중에
    커서나 요약을 붙일 자리가 남고, `data` 가 항상 객체라 FE 파싱이 한결같다.

    **오래된 순이다** (명세 예시의 `de_001 → de_003`). 용량 사다리를 위에서 아래로
    읽는 화면이라 역순으로 주면 FE 가 뒤집어야 한다.
    """

    events: list[DoseEvent]


class MedicationRegisterResponse(CamelModel):
    """`POST /medications` 응답. 명세의 14필드다.

    `GET /medications/current` 이 **이걸 상속한다** (명세: "동일 구조"). 그래서 필드가
    같고, 조회에 대응물이 없는 `doseChanged` · `doseEvent` · `stageChanged` 는
    그쪽에서 고정값이 된다 (`CurrentMedicationResponse` 참고).

    명세에 없는 `effectiveFrom` 은 내리지 않는다. 현재 용량으로 바꾼 날은
    `GET /medications/dose-events` 와 `doseEvent.effectiveFrom` 에 들어 있다.
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
    decided_at: KstDatetime = Field(
        description="판정 시각. `+09:00` 으로 나간다 (규약: ISO 8601 +09:00)."
    )


class CurrentMedicationResponse(MedicationRegisterResponse):
    """`GET /medications/current`.

    명세가 한 줄로 끝낸다 — **"`POST /medications` 응답과 동일 구조."** 그래서 필드를
    새로 적지 않고 **상속한다.** 따로 나열하면 두 스키마가 조용히 갈라진다. 실제로
    갈라져 있었다 — 이쪽만 `effectiveFrom` 을 들고 있었고 명세 14필드 중 7개가 없었다.

    `medicationId` 가 필수라 **투약 기록이 있어야 만들 수 있다.** 기록이 없는 사용자는
    이 응답을 만들 수 없고 `STAGE_NOT_SET` 으로 나간다 (명세 `GET /medications/current`).

    아래 넷은 **구조상 늘 같은 값이다.** 쓰기 결과를 담는 자리라 조회에는 대응물이
    없다. 빼지 않는 건 FE 가 파서를 하나만 쓰게 하려고 명세가 같은 모양을 요구해서다.
    설명을 덮어써 두는 건 상속한 문구("이번 요청으로 …")가 조회에서는 거짓이라
    openapi.json 이 FE 에게 잘못 말하기 때문이다.

    **기본값을 주지 않는다.** 값이 정해져 있다고 `= False` 를 달면 openapi 의
    `required` 에서 빠져 FE 코드 생성기가 선택 필드로 만든다 — 늘 실려 나가는데
    FE 만 `undefined` 분기를 떠안는다. 서비스가 매번 명시적으로 채운다.
    """

    dose_changed: bool = Field(
        description="조회는 아무것도 바꾸지 않는다 — 늘 false 다."
    )
    dose_event: DoseEvent | None = Field(
        description="이번 요청으로 생긴 변경이 없다 — 늘 null 이다."
    )
    stage_changed: bool = Field(
        description="조회는 아무것도 바꾸지 않는다 — 늘 false 다."
    )
    decided_at: KstDatetime = Field(
        description=(
            "단계를 판정한 시각 = 조회 시각. 저장된 단계를 읽는 게 아니라 **매 조회마다"
            " 오늘 기준으로 다시 판정**하기 때문에 지금이 맞다 (`get_current_view`)."
        )
    )
