"""홈(GET /home) 응답 스키마.

필드 이름·구조는 API 명세 §7 그대로다 — 명세에 없는 필드(quantityLabel 등)를 두지 않는다.
today.meals 항목에는 stage 가 없어 schemas/meal.py 의 MealListItem 을 재사용하지 않는다.
"""

import uuid
from datetime import date

from app.models.enums import DrugName, MealType, MedicationStage
from app.schemas.base import CamelModel, KstDatetime
from app.schemas.meal import MealScores


class HomeMedication(CamelModel):
    """투약 카드. 값은 GET /medications/current 와 같은 계산에서 나온다."""

    drug_name: DrugName
    dose_mg: float
    dose_count: int
    stage: MedicationStage
    next_dose_date: date
    days_until_next_dose: int
    dose_change_scheduled: bool


class HomeStomach(CamelModel):
    """위 게이지. satiety_after 가 기록된 가장 최근 식사 기준.

    satiety_pct 는 그 식사의 가장 최근 사후 체크인 값이고, 체크인이 없으면 satiety_after 다.
    feedback_summary 는 그 식사의 피드백이 SAFE 일 때만 채운다.
    """

    satiety_pct: int
    source_meal_id: uuid.UUID
    source_meal_at: KstDatetime
    minutes_since_meal: int
    feedback_summary: str | None


class HomeMeal(CamelModel):
    """오늘의 식사 항목 하나. 평가 전이면 scores 는 None."""

    meal_id: uuid.UUID
    meal_type: MealType
    eaten_at: KstDatetime
    display_name: str
    thumbnail_url: str | None
    scores: MealScores | None


class HomeToday(CamelModel):
    """오늘(KST) 기록한 식사와 아직 기록하지 않은 끼니."""

    recorded_count: int
    meals: list[HomeMeal]
    missing_meal_types: list[MealType]


class HomeResponse(CamelModel):
    """GET /home 응답.

    투약 미등록이면 medication 은 None, 기준 식사가 없으면 stomach 는 None 이다.
    """

    date: date
    medication: HomeMedication | None
    stomach: HomeStomach | None
    today: HomeToday
