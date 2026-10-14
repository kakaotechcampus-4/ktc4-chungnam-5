"""BE ↔ AI 요청·응답 계약.

`ai-stub/schemas.py` 의 구조·필드명을 그대로 따른다. BE 가 이 모양으로 이미 구현돼 있다.

JSON 은 camelCase, 파이썬은 snake_case 다. `_Camel` 이 둘을 이어 준다.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ─────────────────────────── Common ───────────────────────────


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


class QQS(_Camel):
    """BE Rule Engine 의 점수. **null 은 "못 쟀다" 다** — 0(바닥)과 다르다.

    근거가 없는 축은 BE 가 채점하지 않고 null 로 둔다 — 성분을 못 구했거나 체중 기록이
    없는 경우 등(`backend/app/services/evaluation/rule_engine.py`). AI 는 null 인 축을
    평가하는 문장을 쓰지 않는다.
    """

    quantity: float | None
    quality: float | None
    satiety: float | None


# ─────────────────────────── /analyze-meal ───────────────────────────


class AnalyzeMealRequest(_Camel):
    meal_id: str
    meal_type: MealType
    eaten_at: dt.datetime
    stage: Stage
    image_url: str | None = None  # presigned URL. AI 는 S3 권한이 없다
    raw_text: str | None = None  # 이미지와 함께 올 수도 있다

    @model_validator(mode="after")
    def _need_image_or_text(self) -> AnalyzeMealRequest:
        if not self.image_url and not self.raw_text:
            raise ValueError("imageUrl 과 rawText 중 최소 하나는 있어야 한다")
        return self


class RecognizedItem(_Camel):
    """meal_items 한 행에 대응한다.

    성분(kcal·단백질) 필드는 일부러 없다. AI 는 음식을 지목만 하고
    성분은 BE 가 food_refs 에서 채운다. 양도 g 가 아니라 자연 단위로 준다.
    길이·범위 제한은 BE 가 저장 전에 거르는 기준(meal_items 컬럼)과 같다.
    """

    # 가장 일반적인 표준 음식명. 수식어·브랜드명은 뺀다
    original_food_name: str = Field(min_length=1, max_length=255)
    estimated_amount: float = Field(ge=0, le=999999.99)
    unit: str = Field(min_length=1, max_length=32)  # g / 개 / ml … g 환산은 BE 가 한다
    confidence: float = Field(ge=0, le=1)
    # BE /internal/v1 search_foods 로 찾아 채운다. 그 API 가 생기기 전까지는 None
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
    amount: float | None  # null 이면 먹은 양을 모른다. 음식을 빼지 않고 보낸다
    unit: str | None
    nutrition: Nutrition | None = None  # null 이면 성분을 못 구했다


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
    stage: Stage  # 채점 시점의 단계다. 점수와 같은 기준으로 문장을 쓴다
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
    """다음 끼니 제안 한 건. nutrients 는 여기 없다 — BE 가 food_refs 에서 채운다.

    `candidate_food_ref_id` 가 food_refs 에 없으면 BE 는 id 만 버리고 문구는 살린다.
    """

    food_name: str
    advice: str
    # BE /internal/v1 search_foods 로 찾아 채운다. 그 API 가 생기기 전까지는 None
    candidate_food_ref_id: str | None = None


class ShortFeedbackResponse(_Camel):
    body: str  # 공개 API 에서는 summary 로 나간다
    model_version: str
    safety_status: SafetyStatus
    reasoning: str | None = None
    suggestions: list[Suggestion] | None = None  # scope=DAILY 면 null


# ─────────────────────────── /long-feedback ───────────────────────────
# TODO: 계약 미확정. 확정되면 ai-stub/schemas.py 구조를 기준으로 작성한다.
