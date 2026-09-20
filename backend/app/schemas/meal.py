"""식사(meal) 관련 API 요청/응답 스키마."""

import uuid
from datetime import date, datetime

from app.models.enums import MealType, MedicationStage
from app.schemas.base import CamelModel


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
