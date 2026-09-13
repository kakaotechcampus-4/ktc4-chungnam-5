"""식사(meal) 관련 API 요청/응답 스키마."""

import uuid
from datetime import datetime

from app.models.enums import MealStatus, MealType
from app.schemas.base import CamelModel


class MealListItem(CamelModel):
    """GET /meals 목록의 항목 하나."""

    id: uuid.UUID
    meal_type: MealType
    image_key: str | None
    eaten_at: datetime
    status: MealStatus
    created_at: datetime


class MealListResponse(CamelModel):
    """GET /meals 응답 전체."""

    items: list[MealListItem]
    next_cursor: str | None
    has_more: bool
