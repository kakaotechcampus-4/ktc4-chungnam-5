"""식사(meal) 관련 API 요청/응답 스키마."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import Field, StringConstraints

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
