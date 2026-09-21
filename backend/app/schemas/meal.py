"""식사(meal) 관련 API 요청/응답 스키마."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from app.models.enums import MealStatus, MealType, MedicationStage
from app.schemas.base import CamelModel
from app.schemas.nutrition import NutritionInfo


class MealScores(CamelModel):
    """Q/Q/S 점수. 아직 평가 전이면 통째로 None."""

    quantity: int | None
    quality: int | None
    satiety: int | None


class MealListItem(CamelModel):
    """GET /meals 목록의 항목 하나."""

    meal_id: uuid.UUID
    meal_type: MealType
    eaten_at: datetime
    stage: MedicationStage
    display_name: str
    thumbnail_url: str | None
    scores: MealScores | None


class MealListResponse(CamelModel):
    """GET /meals 응답 전체."""

    items: list[MealListItem]
    next_cursor: str | None
    has_more: bool


class AffectedInsight(CamelModel):
    """식사 삭제로 낡은 정보가 된 장기 피드백 하나. (7·8번 insights 구현 전까지는 항상 빈 배열)"""

    period: str
    stale: bool
    stale_reason: str


class MealDeleteResponse(CamelModel):
    """DELETE /meals/{mealId} 응답."""

    meal_id: uuid.UUID
    deleted_at: datetime
    affected_insights: list[AffectedInsight]


class CalendarDay(CamelModel):
    """달력의 날짜 하나 (KST 기준 하루)."""

    date: date
    count: int
    recorded_meal_types: list[MealType]
    stage: MedicationStage


class CalendarSummary(CamelModel):
    """그 달 전체 요약."""

    total_meals: int
    avg_scores: MealScores


class MealCalendarResponse(CamelModel):
    """GET /meals/calendar 응답."""

    month: str
    days: list[CalendarDay]
    summary: CalendarSummary


# meal_items.confirmed_amount_g 는 Numeric(8, 2) 다 — 최대 999999.99.
# 스키마에서 막지 않으면 큰 값이 두 갈래로 500 이 된다: quantize 가
# InvalidOperation 을 던지거나, 통과하더라도 INSERT 가 DataError 로 죽는다.
# 클라이언트 입력 오류는 4xx 여야 한다(core/response.py 의 규칙).
_MAX_AMOUNT = Decimal("999999.99")


class MealItemCreateRequest(CamelModel):
    """POST /meals/{mealId}/items 요청.

    `amount` + `unit` 은 항상 함께 온다. g 으로 환산할 수 있는지는 서버가 판단한다
    (`services.meal.to_grams`) — "2개" 처럼 환산 근거가 없는 단위도 유효한 입력이다.
    """

    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    amount: Annotated[Decimal, Field(gt=0, le=_MAX_AMOUNT)]
    unit: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
    ]


class MealItemCreateResponse(CamelModel):
    """POST /meals/{mealId}/items 응답.

    `status` · `isRecalculation` 은 추가된 항목이 아니라 **식사 전체**의 상태다.
    항목 추가가 재분석을 유발하므로 FE 가 곧바로 폴링으로 넘어갈 수 있게 함께 싣는다.
    """

    item_id: uuid.UUID
    matched: bool
    nutrition: NutritionInfo | None
    status: MealStatus
    is_recalculation: bool


class MealItemUpdate(CamelModel):
    """PATCH /meals/{mealId}/items 요청의 항목 하나.

    세 필드가 모두 필수다 — 항목 하나를 통째로 교체하는 모양이다. FE 는 확인 화면에
    이미 세 값을 다 들고 있고, `amount` 와 `unit` 이 짝이라 한쪽만 오는 애매한 입력이
    생기지 않는다. 부분 수정은 **항목 단위**로 이뤄진다: 고친 항목만 배열에 담는다.
    """

    item_id: uuid.UUID
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    amount: Annotated[Decimal, Field(gt=0, le=_MAX_AMOUNT)]
    unit: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
    ]


class MealItemsUpdateRequest(CamelModel):
    """PATCH /meals/{mealId}/items 요청."""

    # 상한이 없으면 아무 UUID 로나 수만 건을 보내 공공 DB 이름 매칭을 그만큼
    # 돌릴 수 있다 — 소유권 확인(404)보다 매칭이 먼저 돈다
    # (`endpoints/meal_items.py` 의 `_resolve_update`). 한 끼 확인 화면에
    # 담길 수 있는 항목 수를 넉넉히 잡은 값이다.
    items: Annotated[list[MealItemUpdate], Field(min_length=1, max_length=50)]

    @field_validator("items")
    @classmethod
    def _reject_duplicate_items(
        cls, items: list[MealItemUpdate]
    ) -> list[MealItemUpdate]:
        """같은 항목이 두 번 오면 거절한다.

        어느 값이 맞는지 서버가 정할 근거가 없다. 뒤엣것으로 덮으면 사용자가 보낸
        값 하나가 조용히 사라지고, 앞엣것을 쓰면 반대가 된다 — 둘 다 사용자는
        모른다. 클라이언트의 버그이므로 422 로 돌려보낸다.
        """
        seen = {item.item_id for item in items}
        if len(seen) != len(items):
            raise ValueError("같은 itemId 가 두 번 이상 왔습니다.")
        return items


class AnalysisStep(CamelModel):
    """분석 진행 단계 하나. 명세서의 `steps` 배열 원소.

    `frozen=True` 다 — 아래 `RECALCULATION_STEPS` 가 인스턴스를 모든 응답이
    공유하므로, 누군가 한 응답에서 고치면 그 뒤 모든 응답이 바뀐다.
    """

    model_config = CamelModel.model_config | {"frozen": True}

    key: Literal["FOOD_RECOGNITION", "DB_MATCHING", "STAGE_RULE_APPLY"]
    state: Literal["DONE", "RUNNING", "PENDING"]


# 명세서(contracts/API.md PATCH /meals/{mealId}/items)의 예시를 그대로 고정한 값이다.
#
# ⚠️ **실시간 상태가 아니다.** 이 엔드포인트는 큐에 아무것도 넣지 않으므로
# `DB_MATCHING: RUNNING` 이라고 나가도 실제로 도는 작업은 없다 — 공공 DB 매칭은
# 요청을 처리하는 동안 동기로 이미 끝난다. FE 가 이 값을 보고
# `GET /meals/{mealId}` 폴링을 시작하면 상태는 사용자가 [확인] 을 눌러
# `POST /meals/{mealId}/confirm` 이 불릴 때까지 영원히 그대로다.
#
# 진짜 진행상황을 내보내려면 값이 아니라 이 설계를 먼저 바꿔야 한다
# (`endpoints/meal_items.py` 의 `update_meal_items` 독스트링 참고).
RECALCULATION_STEPS: tuple[AnalysisStep, ...] = (
    AnalysisStep(key="FOOD_RECOGNITION", state="DONE"),
    AnalysisStep(key="DB_MATCHING", state="RUNNING"),
    AnalysisStep(key="STAGE_RULE_APPLY", state="PENDING"),
)


class MealItemsUpdateResponse(CamelModel):
    """PATCH /meals/{mealId}/items 응답.

    POST 와 달리 고친 항목을 되돌려주지 않는다 — 명세서가 식사 전체의 상태만
    요구한다. 항목의 최신 모양이 필요하면 FE 는 `GET /meals/{mealId}` 를 부른다.
    """

    status: MealStatus
    is_recalculation: bool
    steps: list[AnalysisStep]
