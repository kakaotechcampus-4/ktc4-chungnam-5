"""식사(meal) 관련 API 요청/응답 스키마."""

import uuid
from datetime import datetime

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
