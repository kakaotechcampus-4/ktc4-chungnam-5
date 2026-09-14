"""BE ↔ AI 요청·응답 계약.

AI 레이어가 아직 없어서, BE 가 개발하는 동안 이 모양에 맞춰 코드를 쓴다.
AI 팀이 실제 서비스를 만들 때 이 파일이 기준이 된다.

JSON 은 camelCase, 파이썬은 snake_case 다. `_Camel` 이 둘을 이어 준다.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class MealType(str, Enum):
    BREAKFAST = "BREAKFAST"
    LUNCH = "LUNCH"
    DINNER = "DINNER"
    SNACK = "SNACK"


class Stage(str, Enum):
    PRE_DOSE = "PRE_DOSE"
    INITIAL = "INITIAL"
    TITRATION = "TITRATION"
    MAINTENANCE = "MAINTENANCE"


class SafetyStatus(str, Enum):
    SAFE = "SAFE"
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class Scope(str, Enum):
    MEAL = "MEAL"
    DAILY = "DAILY"


class PeriodType(str, Enum):
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    ALL = "ALL"  # 공개 API 의 ?period=all. DB ENUM 에는 아직 없다


class QQS(_Camel):
    quantity: float
    quality: float
    satiety: float


# ─────────────────────────── /analyze-meal ───────────────────────────


class AnalyzeMealRequest(_Camel):
    meal_id: str
    meal_type: MealType
    eaten_at: dt.datetime
    stage: Stage
    image_url: str | None = None  # presigned URL. AI 는 S3 권한이 없다
    raw_text: str | None = None

    @model_validator(mode="after")
    def _need_image_or_text(self) -> AnalyzeMealRequest:
        if not self.image_url and not self.raw_text:
            raise ValueError("imageUrl 과 rawText 중 최소 하나는 있어야 한다")
        return self


class RecognizedItem(_Camel):
    """meal_items 한 행에 대응한다.

    성분(kcal·단백질) 필드는 일부러 없다. AI 는 음식을 지목만 하고
    성분은 BE 가 food_refs 에서 채운다. 양도 g 가 아니라 자연 단위로 준다.
    """

    original_food_name: str
    estimated_amount: float
    unit: str  # g / 개 / ml … g 환산은 BE 가 한다
    confidence: float = Field(ge=0, le=1)
    candidate_food_ref_id: str | None = None
    clarify_question: str | None = None


class AnalyzeMealResponse(_Camel):
    meal_id: str
    model_version: str
    safety_status: SafetyStatus
    items: list[RecognizedItem] = Field(default_factory=list)


# ─────────────────────────── /short-feedback ───────────────────────────


class Nutrition(_Camel):
    """BE → AI 방향. AI 가 생성하는 값이 아니다."""

    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carb_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None


class FeedbackItem(_Camel):
    display_name: str
    amount: float
    unit: str
    nutrition: Nutrition | None = None


class SatietyCheckin(_Camel):
    checkin_offset_hours: float
    satiety_pct: int = Field(ge=0, le=100)


class SatietyContext(_Camel):
    before_pct: int | None = Field(default=None, ge=0, le=100)
    after_pct: int | None = Field(default=None, ge=0, le=100)
    checkins: list[SatietyCheckin] = Field(default_factory=list)
    hunger_return_minutes: int | None = None
    user_comment: str | None = None


class DailyMealSummary(_Camel):
    meal_type: MealType
    summary: str
    qqs: QQS


class ShortFeedbackRequest(_Camel):
    scope: Scope
    user_id: str
    stage: Stage
    qqs: QQS  # 채점은 BE Rule Engine 이 이미 끝냈다

    # scope=MEAL
    meal_id: str | None = None
    items: list[FeedbackItem] = Field(default_factory=list)
    satiety: SatietyContext | None = None

    # scope=DAILY
    date: dt.date | None = None
    meals: list[DailyMealSummary] = Field(default_factory=list)

    @model_validator(mode="after")
    def _scope_fields(self) -> ShortFeedbackRequest:
        if self.scope is Scope.MEAL and not self.meal_id:
            raise ValueError("scope=MEAL 에는 mealId 가 필요하다")
        if self.scope is Scope.DAILY and self.date is None:
            raise ValueError("scope=DAILY 에는 date 가 필요하다")
        return self


class Suggestion(_Camel):
    """nutrients 는 여기 없다 — BE 가 food_refs 에서 채운다."""

    food_name: str
    advice: str
    candidate_food_ref_id: str | None = None


class ShortFeedbackResponse(_Camel):
    body: str  # 공개 API 에서는 summary 로 나간다
    model_version: str
    safety_status: SafetyStatus
    reasoning: str | None = None
    suggestions: list[Suggestion] | None = None  # scope=DAILY 면 null


# ─────────────────────────── /long-feedback ───────────────────────────


class SeriesPoint(_Camel):
    date: dt.date
    quantity: float
    quality: float
    satiety: float


class LongFeedbackRequest(_Camel):
    user_id: str
    period_type: PeriodType
    period_start: dt.date
    period_end: dt.date
    stage: Stage
    series: list[SeriesPoint] = Field(default_factory=list)
    daily_summaries: list[str] = Field(default_factory=list)


class LongFeedbackResponse(_Camel):
    """chartData 는 없다 — BE 가 qqs_evaluations 를 집계해 채운다."""

    trend_summary: str
    recommendation: str
    model_version: str
    safety_status: SafetyStatus
