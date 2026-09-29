"""대시보드(dashboard) 관련 API 응답 스키마."""

from datetime import date
from typing import Literal

from app.models.enums import MealType, MedicationStage
from app.schemas.base import CamelModel
from app.schemas.meal import MealScores


class DashboardPeriod(CamelModel):
    """조회 기간 정보. period=all 이면 from 이 None."""

    type: Literal["7d", "28d", "all"]
    from_: date | None
    to: date


class DashboardSeriesPoint(MealScores):
    """날짜 하나의 Q/Q/S 평균 (MealScores 의 quantity/quality/satiety 를 그대로 물려받는다)."""

    date: date
    stage: MedicationStage


class MonthlyAverage(MealScores):
    """월 하나의 평균 Q/Q/S."""

    month: str


class WeightPoint(CamelModel):
    """날짜 하나의 대표 체중 (그날 마지막 기록 기준)."""

    date: date
    weight_kg: float


class StageChange(CamelModel):
    """투약 단계가 바뀐 시점 하나."""

    date: date
    from_: MedicationStage
    to: MedicationStage


class DashboardResponse(CamelModel):
    """GET /dashboard 응답."""

    period: DashboardPeriod
    series: list[DashboardSeriesPoint]
    averages: MealScores
    by_meal_type: dict[MealType, MealScores]
    monthly_averages: list[MonthlyAverage]
    weight_series: list[WeightPoint]
    stage_changes: list[StageChange]
